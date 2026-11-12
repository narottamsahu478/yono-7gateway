import asyncio
import aiohttp
import time
import re
import logging
import globals
from config import FOUR_SIM_API_KEY, TEMPOTP_API_KEY, TEMPORASMS_API_KEY
from spyeye import spyeye_client
from utils.google_sheet import log_otp_history
from utils.helpers import (
    log_game_metric,
    add_error_log,
    add_timeline_event,
    update_live_status,
    upsert_live_success_record,
    terminate_gateway_order_async,
    complete_number_once,
)
from game_sequence import get_game_sequence
from primary_game import get_primary_game
from provider.tempotp import TempOTPProvider
from provider.temporasms import TemporaSMSProvider
from provider.otpindia import OTPIndiaProvider
from provider.uotp import UOTPProvider
from provider.lumera import LumeraProvider
from config import LUMERA_API_KEY
from config import UOTP_API_KEY
from config import OTP_INDIA_API_KEY
from utils import otp_state_db


async def fetch_all_inbox_otps_with_time(txn_id, used_otps):
    all_codes = []

    try:
        async with aiohttp.ClientSession() as session:
            if globals.global_provider == "otpdoctor":
                url = f"https://otpdoctor.in/stubs/handler_api.php?action=getStatus&api_key={globals.global_otpdoctor_api_key}&id={txn_id}"
                async with session.get(url, timeout=5) as response:
                    raw_text = (await response.text()).strip()
                    if raw_text.startswith("STATUS_OK:"):
                        sms_text = raw_text.split(":", 1)[1]
                        all_codes = re.findall(r"(?<!\d)\d{4,6}(?!\d)", sms_text)
            elif globals.global_provider == "temporasms":
                raw_text = await TemporaSMSProvider(TEMPORASMS_API_KEY).get_status(session, str(txn_id))
                if raw_text.startswith("STATUS_OK:"):
                    sms_text = raw_text.split(":", 1)[1]
                    all_codes = re.findall(r"(?<!\d)\d{4,6}(?!\d)", sms_text)
            elif globals.global_provider == "otpindia":
                raw_text = await OTPIndiaProvider(OTP_INDIA_API_KEY).get_status(session, str(txn_id))
                if raw_text.startswith("STATUS_OK:"):
                    all_codes = re.findall(r"(?<!\d)\d{4,6}(?!\d)", raw_text.split(":", 1)[1])
            elif globals.global_provider == "lumera":
                raw_text = await LumeraProvider(LUMERA_API_KEY).get_status(session, str(txn_id))
                if raw_text.startswith("STATUS_OK:"):
                    all_codes = re.findall(r"(?<!\d)\d{4,6}(?!\d)", raw_text.split(":", 1)[1])
            elif globals.global_provider == "uotp":
                raw_text = await UOTPProvider(UOTP_API_KEY).get_status(session, str(txn_id))
                # Diagnostic metadata only: never print OTP, SMS, phone, ID or API key.
                import logging
                status_prefix = raw_text.split(":", 1)[0].strip()
                safe_status = status_prefix if status_prefix in {
                    "STATUS_OK", "STATUS_WAIT_CODE", "STATUS_WAIT_RETRY",
                    "STATUS_CANCEL", "STATUS_FINISH", "NO_ACTIVATION",
                    "BAD_KEY", "ERROR_SQL", "BAD_STATUS",
                } else "UNRECOGNIZED"
                logging.getLogger(__name__).warning(
                    "UOTP polling: status=%s response_length=%d has_status_ok=%s",
                    safe_status, len(raw_text), raw_text.startswith("STATUS_OK:")
                )
                if raw_text.startswith("STATUS_OK:"):
                    all_codes = re.findall(r"(?<!\d)\d{4,6}(?!\d)", raw_text.split(":", 1)[1])
                    logging.getLogger(__name__).warning(
                        "UOTP parser: extracted_code_count=%d", len(all_codes)
                    )
            elif globals.global_provider == "tempotp":
                res = await TempOTPProvider(TEMPOTP_API_KEY).check_sms(session, str(txn_id))
                # TEMPOTP documents the endpoint but not a fixed response schema;
                # search common SMS/code fields recursively and fall back to the
                # serialized response text.
                def _collect_text(obj):
                    values = []
                    if isinstance(obj, dict):
                        for key, value in obj.items():
                            if str(key).lower() in {"sms", "code", "otp", "message", "sms_text", "text"}:
                                values.append(str(value))
                            values.extend(_collect_text(value))
                    elif isinstance(obj, list):
                        for value in obj:
                            values.extend(_collect_text(value))
                    elif isinstance(obj, str):
                        values.append(obj)
                    return values

                texts = _collect_text(res)
                if isinstance(res, str):
                    texts = [res]
                sms_text = " ".join(texts)
                all_codes = re.findall(r"(?<!\d)\d{4,6}(?!\d)", sms_text)
            else:
                url = f"https://api.4sim.st/checkSms?apikey={FOUR_SIM_API_KEY}&id={txn_id}"
                async with session.get(url, timeout=5) as response:
                    res = await response.json()
                    sms_text = str(res.get("sms") or res.get("code") or "")
                    if sms_text:
                        all_codes = re.findall(r"(?<!\d)\d{4,6}(?!\d)", sms_text)

            if all_codes:
                entries = []
                for code in dict.fromkeys(all_codes):
                    first_seen, consumed = otp_state_db.observe(globals.global_provider, txn_id, code)
                    if code not in used_otps and not consumed:
                        entries.append({"code": code, "time": first_seen})
                return entries
    except Exception as exc:
        if globals.global_provider == "uotp":
            import logging
            logging.getLogger(__name__).warning(
                "UOTP polling exception: %s", type(exc).__name__
            )
    return []


async def route_global_otp(
    phone,
    otp,
    otp_arrival_time,
    session,
    active_requests_tracker,
    used_otps,
    tested_game_otps,
    success_chains_count,
    registered_games_list,
    game_balances_map,
    number_state=None,
    provider_threshold=None,
    txn_id=None,
    poller_stop_event=None,
):
    if otp in used_otps:
        if globals.global_provider == "uotp":
            logging.getLogger(__name__).warning("UOTP route: skipped=already_used")
        return False

    if number_state is not None and number_state.get("number_completed"):
        if globals.global_provider == "uotp":
            logging.getLogger(__name__).warning("UOTP route: skipped=number_completed")
        return False

    # Full sequence order mapping: Primary game at top, followed by game_sequence.json
    full_sequence = (number_state or {}).get("game_sequence_snapshot")
    if full_sequence is None:
        full_sequence = [get_primary_game()] + [
            g for g in get_game_sequence() if g != get_primary_game()
        ]
    sequence_index_map = {game: idx for idx, game in enumerate(full_sequence)}

    # Only the current foreground game (and games before it) may receive an OTP.
    # Background requests from upcoming games must never steal an OTP that belongs
    # to the current/previous part of the registration sequence.
    pending = list(active_requests_tracker.items())
    if globals.global_provider == "uotp":
        logging.getLogger(__name__).warning("UOTP route: pending_requests=%d", len(pending))
    pending.sort(
        key=lambda item: (
            sequence_index_map.get(item[0], 999),
            item[1].get("send_time", 0),
        ),
        reverse=True,
    )

    foreground = [
        (game_key, track_data)
        for game_key, track_data in pending
        if not track_data.get("background_mode")
        and not track_data.get("completed")
    ]
    current_sequence_index = None
    if foreground:
        current_game_key, _ = max(
            foreground,
            key=lambda item: (
                sequence_index_map.get(item[0], -1),
                item[1].get("send_time", 0),
            ),
        )
        current_sequence_index = sequence_index_map.get(current_game_key)

    # Once a verification attempt is rejected as invalid, the same OTP may only
    # fall back to earlier games in the sequence. It must never move forward to
    # MBMBet/Maha/etc. and it must not be marked globally used, because the current
    # game may still receive its real OTP (for example 0147 after invalid 3209).
    max_allowed_sequence_index = (
        current_sequence_index if current_sequence_index is not None else 999999
    )

    for game_key, track_data in pending:
        if game_key not in active_requests_tracker or track_data.get("completed"):
            continue
        if (otp, game_key) in tested_game_otps:
            if globals.global_provider == "uotp":
                logging.getLogger(__name__).warning("UOTP route: skipped=previously_tested game=%s", game_key)
            continue

        send_time = track_data.get("send_time", 0)
        sequence_index = sequence_index_map.get(game_key, 999999)

        # Never route an OTP to a game after the current foreground game.
        if sequence_index > max_allowed_sequence_index:
            if globals.global_provider == "uotp":
                logging.getLogger(__name__).warning("UOTP route: skipped=sequence_filter game=%s", game_key)
            continue

        # Strict timestamp gate: a code first observed before this request
        # cannot be assigned to the request, regardless of sequence position.
        # This uses persisted first-observed time, not each poll's time.
        if send_time > otp_arrival_time:
            if globals.global_provider == "uotp":
                logging.getLogger(__name__).warning("UOTP route: skipped=sent_after_poll game=%s", game_key)
            continue

        # Claim the attempt durably BEFORE making an external verification call.
        if txn_id is not None and not otp_state_db.claim_attempt(globals.global_provider, txn_id, otp, game_key):
            continue
        tested_game_otps.add((otp, game_key))
        ui_name = track_data["ui_name"]
        result_future = track_data.get("result_future")

        try:
            track_data["otp_received"] = True
            from utils.cancel_registry import mark_otp
            mark_otp(globals.global_provider, txn_id)

            await update_live_status(
                phone, f"Global Route {otp} -> {ui_name}", progress_val=70
            )
            if globals.global_provider == "uotp":
                logging.getLogger(__name__).warning("UOTP route: verification_attempt game=%s", game_key)
            verify_res = await spyeye_client.verify_otp(
                session, track_data["api_name"], track_data["request_id"], otp
            )
            v_msg = str(
                verify_res.get("msg") or verify_res.get("message") or ""
            ).lower()
            v_time = time.time()
            if globals.global_provider == "uotp":
                logging.getLogger(__name__).warning("UOTP route: verification_response game=%s success=%s", game_key, bool(verify_res.get("success") is True or verify_res.get("status") == "success"))

            if any(
                x in v_msg
                for x in (
                    "timeout",
                    "gateway",
                    "server error",
                    "internal error",
                    "connection",
                    "temporarily unavailable",
                    "502",
                    "504",
                )
            ):
                track_data["verify_response_lost"] = True
                await add_error_log(
                    phone, ui_name, f"Verify uncertain response: {v_msg}"
                )
                await update_live_status(
                    phone, f"{ui_name} Verify Uncertain", progress_val=75
                )
                continue

            if any(
                x in v_msg
                for x in ("already used", "otp used", "duplicate", "consumed")
            ):
                used_otps.add(otp)
                if txn_id is not None:
                    otp_state_db.mark_consumed(globals.global_provider, txn_id, otp)
                break

            if (
                verify_res.get("success") is True
                or verify_res.get("status") == "success"
            ):
                bal_val = (
                    verify_res.get("balance")
                    or verify_res.get("data", {}).get("account_balance", 0)
                )
                bal = "₹" + str(int(float(bal_val)))

                used_otps.add(otp)
                if txn_id is not None:
                    otp_state_db.mark_result(globals.global_provider, txn_id, otp, game_key, 'success')
                    otp_state_db.mark_consumed(globals.global_provider, txn_id, otp)
                if ui_name not in registered_games_list:
                    success_chains_count[0] += 1
                    registered_games_list.append(ui_name)
                game_balances_map[ui_name] = bal
                # Live-update the Success OTP repository immediately for this
                # phone/game, including the verified balance.
                await upsert_live_success_record(phone, ui_name, bal)
                if number_state is not None:
                    number_state["otp_received_anywhere"] = True

                async with globals.stats_lock:
                    if (
                        ui_name
                        not in globals.live_stats["registration_summary"]
                    ):
                        globals.live_stats["registration_summary"][ui_name] = 0
                    globals.live_stats["registration_summary"][ui_name] += 1

                await update_live_status(
                    phone,
                    "Background Success",
                    balance_text=bal,
                    target_game=ui_name,
                    progress_val=100,
                )
                await log_otp_history(
                    phone,
                    ui_name,
                    otp,
                    send_time,
                    otp_arrival_time,
                    v_time,
                    "GLOBAL POLLER SUCCESS",
                )
                await log_game_metric(ui_name, "success")
                await add_timeline_event(
                    phone, f"Global Poller Success -> {ui_name}"
                )

                if (
                    globals.global_provider != "temporasms"
                    and number_state is not None
                    and provider_threshold is not None
                    and success_chains_count[0] >= provider_threshold
                ):
                    track_data["completed"] = True
                    track_data["cleanup_done"] = True
                    if track_data.get("background_mode"):
                        async with globals.background_lock:
                            globals.background_active_tasks = max(
                                0, globals.background_active_tasks - 1
                            )
                    active_requests_tracker.pop(game_key, None)
                    async with globals.tracker_lock:
                        globals.global_active_requests_tracker.pop(
                            f"{phone}_{game_key}", None
                        )
                    if result_future and not result_future.done():
                        result_future.set_result((True, True, bal))

                    await complete_number_once(
                        phone,
                        txn_id,
                        active_requests_tracker,
                        poller_stop_event,
                        number_state,
                        f"{globals.global_provider.upper()} threshold {provider_threshold}",
                    )
                    return True

                track_data["completed"] = True
                track_data["cleanup_done"] = True

                if track_data["background_mode"]:
                    async with globals.background_lock:
                        globals.background_active_tasks = max(
                            0, globals.background_active_tasks - 1
                        )

                active_requests_tracker.pop(game_key, None)
                async with globals.tracker_lock:
                    globals.global_active_requests_tracker.pop(
                        f"{phone}_{game_key}", None
                    )

                if result_future and not result_future.done():
                    result_future.set_result((True, True, bal))
                return True

            if txn_id is not None:
                otp_state_db.mark_result(globals.global_provider, txn_id, otp, game_key, 'invalid_or_failed')
            if "already" in v_msg or "555" in v_msg:
                used_otps.add(otp)
                if txn_id is not None:
                    otp_state_db.mark_result(globals.global_provider, txn_id, otp, game_key, 'already')
                    otp_state_db.mark_consumed(globals.global_provider, txn_id, otp)
                await update_live_status(
                    phone, "Background Already", progress_val=100
                )
                await log_otp_history(
                    phone,
                    ui_name,
                    otp,
                    send_time,
                    otp_arrival_time,
                    None,
                    "GLOBAL POLLER: Already Reg",
                )
                await log_game_metric(ui_name, "already")

                track_data["completed"] = True
                track_data["cleanup_done"] = True

                if track_data["background_mode"]:
                    async with globals.background_lock:
                        globals.background_active_tasks = max(
                            0, globals.background_active_tasks - 1
                        )

                active_requests_tracker.pop(game_key, None)
                async with globals.tracker_lock:
                    globals.global_active_requests_tracker.pop(
                        f"{phone}_{game_key}", None
                    )

                if result_future and not result_future.done():
                    result_future.set_result(("already", True, "₹0"))
                return "already"

            if any(
                x in v_msg
                for x in (
                    "invalid",
                    "wrong",
                    "incorrect",
                    "expired",
                    "verification failed",
                    "invalid verification code",
                )
            ):
                await add_error_log(phone, ui_name, f"Invalid OTP ({otp})")
                await update_live_status(
                    phone, f"{ui_name} -> Invalid OTP", progress_val=45
                )
                # An invalid OTP can only fall back to an earlier game.
                # Never pass it to an upcoming game. Keep the OTP available for
                # the current game because the correct code may arrive later.
                max_allowed_sequence_index = min(
                    max_allowed_sequence_index, sequence_index - 1
                )
                continue

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            track_data["verify_response_lost"] = True
            await add_error_log(
                phone, ui_name, f"Verify Exception Lost : {type(e).__name__}"
            )
            await update_live_status(
                phone, f"{ui_name} Verify Exception", progress_val=75
            )
            continue
        except Exception as e:
            await add_error_log(
                phone, ui_name, f"Global route error: {type(e).__name__}"
            )
            continue

    if globals.global_provider == "uotp":
        logging.getLogger(__name__).warning("UOTP route: no_successful_routing")
    return False


async def global_inbox_poller(
    phone,
    txn_id,
    active_requests_tracker,
    used_otps,
    tested_game_otps,
    success_chains_count,
    registered_games_list,
    game_balances_map,
    poller_stop_event,
    number_state=None,
    provider_threshold=None,
):
    async with aiohttp.ClientSession() as session:
        while not poller_stop_event.is_set():
            if number_state is not None and number_state.get("number_completed"):
                break
            try:
                entries = await fetch_all_inbox_otps_with_time(
                    txn_id, used_otps
                )
                for entry in entries:
                    await route_global_otp(
                        phone,
                        entry["code"],
                        entry["time"],
                        session,
                        active_requests_tracker,
                        used_otps,
                        tested_game_otps,
                        success_chains_count,
                        registered_games_list,
                        game_balances_map,
                        number_state,
                        provider_threshold,
                        txn_id,
                        poller_stop_event,
                    )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if globals.global_provider == "uotp":
                    logging.getLogger(__name__).warning("UOTP route: poller_loop_exception=%s", type(exc).__name__)

            now = time.time()

            for game_key, track in list(active_requests_tracker.items()):
                if number_state is not None and number_state.get("number_completed"):
                    break
                if not track["background_mode"] or track["completed"]:
                    continue

                if now >= track["background_expiry"]:
                    has_received_otp = track.get("otp_received", False)

                    try:
                        await spyeye_client.cancel_request(
                            session, track["api_name"], track["request_id"]
                        )
                    except Exception:
                        pass

                    await terminate_gateway_order_async(
                        txn_id,
                        otp_received=has_received_otp,
                        phone=phone,
                        force_cancel=not has_received_otp,
                    )

                    track["completed"] = True

                    async with globals.background_lock:
                        globals.background_active_tasks = max(
                            0, globals.background_active_tasks - 1
                        )

                    active_requests_tracker.pop(game_key, None)
                    async with globals.tracker_lock:
                        globals.global_active_requests_tracker.pop(
                            f"{phone}_{game_key}", None
                        )

                    await update_live_status(
                        phone, "Background Timeout (Safely Handled)"
                    )

            try:
                await asyncio.wait_for(poller_stop_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
