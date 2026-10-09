"""ADB reverse maintenance uses one fixed device and preserves pre-existing mappings.

Every command is simulated. These tests do not require adb, an Android device,
microphone access, or network access.
"""
import socket
import subprocess
import threading
import unittest
from unittest.mock import Mock, call

from mac_audio_bridge import AdbReverseGuard, listening_socket, resolve_adb_serial


ADB = "/fake/platform-tools/adb"
SERIAL = "phone-fixed"
PORT = 19101
TIMEOUT = 0.25
LIST_COMMAND = [ADB, "-s", SERIAL, "reverse", "--list"]
RESTORE_COMMAND = [
    ADB, "-s", SERIAL, "reverse", "--no-rebind", f"tcp:{PORT}", f"tcp:{PORT}",
]
REMOVE_COMMAND = [ADB, "-s", SERIAL, "reverse", "--remove", f"tcp:{PORT}"]
RUN_OPTIONS = {"capture_output": True, "text": True, "timeout": TIMEOUT}
EXISTING_MAPPING = f"UsbFfs tcp:{PORT} tcp:{PORT}\n"


def result(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class AdbReverseGuardTest(unittest.TestCase):
    def guard(self, run, log_fn=None, interval_s=5):
        return AdbReverseGuard(
            ADB, SERIAL, PORT, run=run,
            log_fn=log_fn if log_fn is not None else Mock(),
            interval_s=interval_s, timeout_s=TIMEOUT,
        )

    def test_existing_mapping_is_checked_without_rebinding(self):
        run = Mock(return_value=result(EXISTING_MAPPING))
        log_fn = Mock()
        guard = self.guard(run, log_fn)

        self.assertTrue(guard.check_once())
        first_log_count = log_fn.call_count
        self.assertTrue(guard.check_once())

        self.assertEqual([call(LIST_COMMAND, **RUN_OPTIONS)] * 2, run.call_args_list)
        self.assertEqual(first_log_count, log_fn.call_count)

    def test_missing_mapping_is_restored_for_the_fixed_device(self):
        run = Mock(side_effect=[result(), result(), result(EXISTING_MAPPING)])
        log_fn = Mock()
        guard = self.guard(run, log_fn)

        self.assertTrue(guard.check_once())
        self.assertGreater(log_fn.call_count, 0)
        self.assertTrue(guard.check_once())

        self.assertEqual([
            call(LIST_COMMAND, **RUN_OPTIONS),
            call(RESTORE_COMMAND, **RUN_OPTIONS),
            call(LIST_COMMAND, **RUN_OPTIONS),
        ], run.call_args_list)

    def test_unrelated_mappings_do_not_hide_a_missing_port(self):
        run = Mock(side_effect=[result("UsbFfs tcp:9999 tcp:9999\n"), result()])

        self.assertTrue(self.guard(run).check_once())

        self.assertEqual([
            call(LIST_COMMAND, **RUN_OPTIONS),
            call(RESTORE_COMMAND, **RUN_OPTIONS),
        ], run.call_args_list)

    def test_conflicting_local_port_is_not_overwritten(self):
        run = Mock(return_value=result(f"UsbFfs tcp:{PORT} tcp:9999\n"))
        log_fn = Mock()
        guard = self.guard(run, log_fn)

        self.assertFalse(guard.check_once())
        first_log_count = log_fn.call_count
        self.assertFalse(guard.check_once())

        self.assertEqual([call(LIST_COMMAND, **RUN_OPTIONS)] * 2, run.call_args_list)
        self.assertEqual(first_log_count, log_fn.call_count)

    def test_offline_device_can_recover_without_repeated_logs(self):
        run = Mock(side_effect=[
            result(returncode=1, stderr="error: device offline"),
            result(returncode=1, stderr="error: device offline"),
            result(), result(), result(EXISTING_MAPPING), result(EXISTING_MAPPING),
        ])
        log_fn = Mock()
        guard = self.guard(run, log_fn)

        self.assertFalse(guard.check_once())
        offline_log_count = log_fn.call_count
        self.assertFalse(guard.check_once())
        self.assertEqual(offline_log_count, log_fn.call_count)
        self.assertTrue(guard.check_once())
        self.assertTrue(guard.check_once())
        healthy_log_count = log_fn.call_count
        self.assertTrue(guard.check_once())
        self.assertEqual(healthy_log_count, log_fn.call_count)

        self.assertEqual([
            call(LIST_COMMAND, **RUN_OPTIONS),
            call(LIST_COMMAND, **RUN_OPTIONS),
            call(LIST_COMMAND, **RUN_OPTIONS),
            call(RESTORE_COMMAND, **RUN_OPTIONS),
            call(LIST_COMMAND, **RUN_OPTIONS),
            call(LIST_COMMAND, **RUN_OPTIONS),
        ], run.call_args_list)

    def test_list_timeout_and_oserror_are_retryable_without_log_spam(self):
        failures = [
            subprocess.TimeoutExpired(LIST_COMMAND, TIMEOUT),
            OSError("simulated adb unavailable"),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                run = Mock(side_effect=[failure, failure, result(EXISTING_MAPPING)])
                log_fn = Mock()
                guard = self.guard(run, log_fn)

                self.assertFalse(guard.check_once())
                first_log_count = log_fn.call_count
                self.assertFalse(guard.check_once())
                self.assertEqual(first_log_count, log_fn.call_count)
                self.assertTrue(guard.check_once())
                self.assertEqual([call(LIST_COMMAND, **RUN_OPTIONS)] * 3, run.call_args_list)

    def test_failed_restore_is_retryable(self):
        failures = [
            result(returncode=1, stderr="error: device offline"),
            subprocess.TimeoutExpired(RESTORE_COMMAND, TIMEOUT),
            OSError("simulated adb unavailable"),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                run = Mock(side_effect=[result(), failure, result(), result()])
                guard = self.guard(run)

                self.assertFalse(guard.check_once())
                self.assertTrue(guard.check_once())
                self.assertEqual([
                    call(LIST_COMMAND, **RUN_OPTIONS),
                    call(RESTORE_COMMAND, **RUN_OPTIONS),
                    call(LIST_COMMAND, **RUN_OPTIONS),
                    call(RESTORE_COMMAND, **RUN_OPTIONS),
                ], run.call_args_list)

    def test_stopped_guard_never_rebuilds_or_removes_mapping(self):
        run = Mock(return_value=result())
        guard = self.guard(run)

        self.assertTrue(guard.stop())
        self.assertFalse(guard.check_once())

        run.assert_not_called()
        self.assertTrue(guard._stop.is_set())

    def test_stop_during_list_prevents_subsequent_restore(self):
        run = Mock()
        guard = self.guard(run)

        def stop_while_listing(*args, **kwargs):
            guard._stop.set()
            return result()

        run.side_effect = stop_while_listing
        self.assertFalse(guard.check_once())
        run.assert_called_once_with(LIST_COMMAND, **RUN_OPTIONS)

    def test_background_guard_stops_promptly_and_leaves_mapping_intact(self):
        checked = threading.Event()

        def list_mapping(*args, **kwargs):
            checked.set()
            return result(EXISTING_MAPPING)

        run = Mock(side_effect=list_mapping)
        guard = self.guard(run, interval_s=3600)
        guard.start()
        try:
            self.assertTrue(checked.wait(1), "background guard did not check the mapping")
            self.assertTrue(guard.stop())
            self.assertFalse(guard._thread.is_alive())
            self.assertTrue(guard.stop(), "stopping twice must remain safe")
            run.assert_called_once_with(LIST_COMMAND, **RUN_OPTIONS)
        finally:
            guard._stop.set()
            if guard._thread is not None:
                guard._thread.join(1)

    def test_preexisting_mapping_is_never_owned_or_removed_on_cleanup(self):
        run = Mock(return_value=result(EXISTING_MAPPING))
        guard = self.guard(run)

        self.assertTrue(guard.check_once())
        self.assertFalse(guard.created_mapping)
        self.assertTrue(guard.stop())
        guard.cleanup_created_mapping()

        self.assertFalse(guard.created_mapping)
        self.assertTrue(all(
            invocation.args[0] == LIST_COMMAND for invocation in run.call_args_list
        ), "preexisting mapping must not be rebound or removed")

    def test_created_mapping_is_removed_only_after_current_target_is_checked(self):
        run = Mock(side_effect=[result(), result(), result(EXISTING_MAPPING), result()])
        guard = self.guard(run)

        self.assertTrue(guard.check_once())
        self.assertTrue(guard.created_mapping)
        self.assertTrue(guard.stop())
        self.assertEqual(2, run.call_count, "stop must not remove the created mapping")
        self.assertTrue(guard.cleanup_created_mapping())

        self.assertEqual([
            call(LIST_COMMAND, **RUN_OPTIONS),
            call(RESTORE_COMMAND, **RUN_OPTIONS),
            call(LIST_COMMAND, **RUN_OPTIONS),
            call(REMOVE_COMMAND, **RUN_OPTIONS),
        ], run.call_args_list)

    def test_created_mapping_is_preserved_if_changed_missing_or_unavailable(self):
        current_states = [
            result(f"UsbFfs tcp:{PORT} tcp:9999\n"),
            result(),
            result(returncode=1, stderr="error: device offline"),
            subprocess.TimeoutExpired(LIST_COMMAND, TIMEOUT),
            OSError("simulated adb unavailable"),
        ]
        for current in current_states:
            with self.subTest(current=current):
                run = Mock(side_effect=[result(), result(), current])
                guard = self.guard(run)

                self.assertTrue(guard.check_once())
                self.assertTrue(guard.created_mapping)
                self.assertTrue(guard.stop())
                guard.cleanup_created_mapping()

                self.assertEqual([
                    call(LIST_COMMAND, **RUN_OPTIONS),
                    call(RESTORE_COMMAND, **RUN_OPTIONS),
                    call(LIST_COMMAND, **RUN_OPTIONS),
                ], run.call_args_list)


class ResolveAdbSerialTest(unittest.TestCase):
    def test_explicit_serial_is_fixed_without_scanning_devices(self):
        run = Mock(side_effect=AssertionError("device scan must not run"))

        self.assertEqual(SERIAL, resolve_adb_serial(
            ADB, serial=SERIAL, run=run, timeout_s=TIMEOUT,
        ))

        run.assert_not_called()

    def test_single_online_device_is_selected_once(self):
        run = Mock(return_value=result(
            "List of devices attached\n"
            "offline-phone\toffline\n"
            f"{SERIAL}\tdevice\n"
            "unauthorized-phone\tunauthorized\n\n"
        ))

        self.assertEqual(SERIAL, resolve_adb_serial(ADB, run=run, timeout_s=TIMEOUT))

        run.assert_called_once_with([ADB, "devices"], **RUN_OPTIONS)

    def test_zero_or_multiple_online_devices_are_rejected(self):
        outputs = [
            "List of devices attached\n\n",
            "List of devices attached\nphone\toffline\n",
            "List of devices attached\nphone\tunauthorized\n",
            "List of devices attached\nphone-1\tdevice\nphone-2\tdevice\n",
        ]
        for stdout in outputs:
            with self.subTest(stdout=stdout):
                run = Mock(return_value=result(stdout))
                with self.assertRaises(ValueError):
                    resolve_adb_serial(ADB, run=run, timeout_s=TIMEOUT)
                run.assert_called_once_with([ADB, "devices"], **RUN_OPTIONS)

    def test_device_scan_failures_raise_valueerror(self):
        failures = [
            result(returncode=1, stderr="simulated adb failure"),
            subprocess.TimeoutExpired([ADB, "devices"], TIMEOUT),
            OSError("simulated adb unavailable"),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                run = Mock(side_effect=[failure])
                with self.assertRaises(ValueError):
                    resolve_adb_serial(ADB, run=run, timeout_s=TIMEOUT)
                run.assert_called_once_with([ADB, "devices"], **RUN_OPTIONS)


class ListeningSocketTest(unittest.TestCase):
    def reach(self, address, port):
        socket.create_connection((address, port), 2).close()

    def test_ipv6_listener_also_accepts_ipv4_and_ipv4_listener_refuses_ipv6(self):
        # A phone resolving <mac>.local gets an IPv6 address first; an IPv4-only listener refuses it.
        both = listening_socket("::", 0)
        self.addCleanup(both.close)
        port = both.getsockname()[1]
        self.reach("127.0.0.1", port)
        self.reach("::1", port)
        only4 = listening_socket("127.0.0.1", 0)
        self.addCleanup(only4.close)
        self.reach("127.0.0.1", only4.getsockname()[1])
        with self.assertRaises(OSError):
            self.reach("::1", only4.getsockname()[1])


if __name__ == "__main__":
    unittest.main()
