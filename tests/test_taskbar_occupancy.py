"""Taskbar accessibility safety policy; no claim of live Windows validation."""

import ctypes
import sys
import threading
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from question_volume.taskbar_layout import Rect
from question_volume import taskbar_occupancy as occupancy
from question_volume.taskbar_occupancy import (
    AutomationNode, BUTTON, CUSTOM, IMAGE, LIST_ITEM, PANE, TEXT,
    WindowsTaskbarInspector, occupied_regions,
)


BAR = Rect(0, 1040, 1920, 1080)
MONITOR = Rect(0, 0, 1920, 1080)


def button(left, right, *, kind=BUTTON, class_name="Taskbar.ExperienceToggleButton", aid=""):
    return AutomationNode(Rect(left, 1040, right, 1080), kind, class_name, aid)


def supported_nodes():
    return (
        AutomationNode(BAR, PANE, "Shell_TrayWnd"),
        AutomationNode(BAR, PANE, "Windows.UI.Composition.DesktopWindowContentBridge"),
        AutomationNode(BAR, PANE, "Taskbar.TaskbarFrame", "TaskbarFrame"),
        button(0, 128, aid="WidgetsButton"),
        button(720, 768, aid="StartButton"),
        button(768, 920, aid="SearchBox"),
        button(920, 968, kind=LIST_ITEM, class_name="Taskbar.TaskListButton"),
        button(968, 1016, kind=LIST_ITEM, class_name="Taskbar.TaskListButton"),
        button(1016, 1064, kind=LIST_ITEM, class_name="Taskbar.TaskListButton"),
        button(1720, 1800, aid="SystemTray"),
        button(1800, 1916, aid="Clock"),
    )


class RegionExtractionTests(unittest.TestCase):
    def test_all_occupied_shell_controls_remain_protected(self):
        nodes = supported_nodes()
        occupied, reliable = occupied_regions(BAR, nodes)
        self.assertTrue(reliable)
        for node in nodes[3:]:
            self.assertIn(node.bounds, occupied)
        self.assertNotIn(BAR, occupied)
        self.assertFalse(any(rect.left < 1700 and rect.right > 1100 for rect in occupied))

    def test_unknown_custom_leaf_text_and_image_are_protected_without_english_names(self):
        nodes = supported_nodes() + (
            AutomationNode(Rect(1200, 1044, 1280, 1078), CUSTOM, "Fabrikam.Widget", "名前"),
            AutomationNode(Rect(1310, 1044, 1350, 1078), TEXT),
            AutomationNode(Rect(1390, 1044, 1410, 1078), IMAGE),
        )
        occupied, reliable = occupied_regions(BAR, nodes)
        self.assertTrue(reliable)
        for node in nodes[-3:]:
            self.assertIn(node.bounds, occupied)

    def test_unknown_wholebar_or_partial_container_is_never_assumed_empty(self):
        unknown = AutomationNode(BAR, PANE, "ThirdParty.Taskbar")
        partial = AutomationNode(Rect(500, 1040, 1400, 1080), PANE, "Taskbar.TaskbarFrame")
        occupied, reliable = occupied_regions(BAR, supported_nodes() + (unknown, partial))
        self.assertTrue(reliable)
        self.assertIn(BAR, occupied)
        self.assertIn(partial.bounds, occupied)

    def test_known_container_can_be_identified_by_automation_id(self):
        node = AutomationNode(BAR, PANE, "", "TaskbarFrame")
        occupied, reliable = occupied_regions(BAR, supported_nodes() + (node,))
        self.assertTrue(reliable)
        self.assertNotIn(BAR, occupied)

    def test_native_clock_tray_and_show_desktop_bounds_supplement_uia(self):
        native = (Rect(1600, 1040, 1916, 1080), Rect(1916, 1040, 1920, 1080))
        occupied, reliable = occupied_regions(BAR, supported_nodes()[:-2], native_occupied=native)
        self.assertTrue(reliable)
        for rect in native:
            self.assertIn(rect, occupied)

    def test_icon_hit_area_is_expanded_to_48_dip_and_clipped(self):
        nodes = (button(0, 16), button(1904, 1920, class_name="Taskbar.TaskListButton"))
        occupied, reliable = occupied_regions(BAR, nodes, dpi=144)
        self.assertTrue(reliable)
        self.assertEqual(occupied, (Rect(0, 1040, 44, 1080), Rect(1876, 1040, 1920, 1080)))

    def test_negative_monitor_coordinates_stay_in_physical_coordinates(self):
        bounds = Rect(-1920, 0, 0, 60)
        nodes = (AutomationNode(Rect(-1200, 0, -1128, 60), BUTTON, "Taskbar.TaskListButton"),
                 AutomationNode(Rect(-1128, 0, -1056, 60), BUTTON))
        occupied, reliable = occupied_regions(bounds, nodes, dpi=144)
        self.assertTrue(reliable)
        self.assertEqual(occupied, tuple(node.bounds for node in nodes))

    def test_empty_root_only_and_one_distinct_control_are_unreliable(self):
        root = AutomationNode(BAR, PANE, "Shell_TrayWnd")
        for nodes in ((), (root,), (button(700, 748),),
                      (button(700, 748), AutomationNode(Rect(700, 1040, 748, 1080), TEXT))):
            with self.subTest(nodes=nodes):
                _, reliable = occupied_regions(BAR, nodes)
                self.assertFalse(reliable)

    def test_native_bounds_do_not_make_an_empty_uia_tree_reliable(self):
        _, reliable = occupied_regions(BAR, (), native_occupied=(Rect(1700, 1040, 1920, 1080),))
        self.assertFalse(reliable)

    def test_partial_tree_exposing_only_start_search_or_app_container_is_unreliable(self):
        nodes = (button(720, 768, aid="StartButton"), button(768, 920, aid="SearchBox"))
        _, reliable = occupied_regions(BAR, nodes)
        self.assertFalse(reliable)
        container = AutomationNode(Rect(920, 1040, 1500, 1080), PANE,
                                   "Taskbar.TaskListItemsControl", "TaskbarFrameRepeater")
        _, reliable = occupied_regions(BAR, nodes + (container,))
        self.assertFalse(reliable)

    def test_app_button_can_be_identified_by_stable_class_or_appid(self):
        for class_name, aid in (("Taskbar.TaskListButton", ""),
                                ("Taskbar.TaskListLabeledButton", ""),
                                ("Taskbar.TaskListButtonAutomationPeer", ""),
                                ("MSTaskListWClass", ""),
                                ("", "Appid: Microsoft.Windows.Explorer")):
            with self.subTest(class_name=class_name, aid=aid):
                nodes = (button(720, 768, aid="StartButton"),
                         button(920, 968, class_name=class_name, aid=aid))
                _, reliable = occupied_regions(BAR, nodes)
                self.assertTrue(reliable)

    def test_noninteractive_containers_cannot_prove_tree_support(self):
        nodes = (AutomationNode(Rect(0, 1040, 300, 1080), PANE),
                 AutomationNode(Rect(300, 1040, 600, 1080), PANE))
        _, reliable = occupied_regions(BAR, nodes)
        self.assertFalse(reliable)

    def test_bad_onscreen_rectangle_unknown_control_and_invalid_dpi_fail_closed(self):
        bad = (
            AutomationNode(Rect(1, 2, 1, 2), CUSTOM),
            AutomationNode(Rect(float("nan"), 1040, 900, 1080), BUTTON),
            AutomationNode(Rect(1100, 1040, 1200, 1080), 0),
        )
        for node in bad:
            with self.subTest(node=node):
                _, reliable = occupied_regions(BAR, supported_nodes() + (node,))
                self.assertFalse(reliable)
        for dpi in (0, 2000):
            _, reliable = occupied_regions(BAR, supported_nodes(), dpi=dpi)
            self.assertFalse(reliable)

    def test_offscreen_controls_are_ignored_without_invalidating_real_tree(self):
        hidden = AutomationNode(Rect(0, 0, 0, 0), CUSTOM, offscreen=True)
        occupied, reliable = occupied_regions(BAR, supported_nodes() + (hidden,))
        self.assertTrue(reliable)
        self.assertNotIn(hidden.bounds, occupied)

    def test_over_limit_tree_and_zero_height_bar_are_unreliable(self):
        _, reliable = occupied_regions(BAR, (button(700, 748),) * (occupancy.MAX_UIA_ELEMENTS + 1))
        self.assertFalse(reliable)
        _, reliable = occupied_regions(Rect(0, 1080, 1920, 1080), supported_nodes())
        self.assertFalse(reliable)


class FakeNative:
    def __init__(self):
        self.rectangle = BAR
        self.visible = True
        self.autohide = False
        self.fullscreen = False
        self.taskbars = Mock(return_value=(1,))
        self.protected_children = Mock(return_value=(Rect(1800, 1040, 1920, 1080),))

    def bounds(self, hwnd):
        return self.rectangle

    def dpi(self, hwnd):
        return 96

    def monitor_bounds(self, hwnd):
        return MONITOR

    def is_visible(self, hwnd):
        return self.visible

    def auto_hide_enabled(self):
        return self.autohide

    def fullscreen_covering(self, monitor):
        return self.fullscreen


class InspectorTests(unittest.TestCase):
    def inspector(self, native=None, automation=None, clock=None):
        native = native or FakeNative()
        automation = automation or SimpleNamespace(descendants=Mock(return_value=supported_nodes()), close=Mock())
        inspector = WindowsTaskbarInspector(_native=native, _automation=automation,
                                            _clock=clock or Mock(return_value=123.0))
        self.addCleanup(inspector.close)
        return inspector, native, automation

    def test_scan_uses_uia_and_native_protection_and_timestamp_precedes_query(self):
        events = []
        automation = SimpleNamespace(descendants=lambda hwnd: events.append("query") or supported_nodes(), close=Mock())
        inspector, _, _ = self.inspector(automation=automation, clock=lambda: events.append("clock") or 23.0)
        snapshots = inspector.scan()
        self.assertEqual(events, ["clock", "query"])
        self.assertEqual(len(snapshots), 1)
        snapshot = snapshots[0]
        self.assertEqual(snapshot.bounds, BAR)
        self.assertEqual(snapshot.observed_at, 23.0)
        self.assertTrue(snapshot.reliable)
        self.assertTrue(snapshot.visible)

    def test_hidden_autohide_offscreen_zeroheight_and_fullscreen_bars_hide_without_uia(self):
        cases = (("visible", False), ("autohide", True), ("fullscreen", True),
                 ("rectangle", Rect(0, 1078, 1920, 1118)),
                 ("rectangle", Rect(0, 1080, 1920, 1080)))
        for attribute, value in cases:
            with self.subTest(attribute=attribute, value=value):
                native = FakeNative()
                setattr(native, attribute, value)
                inspector, _, automation = self.inspector(native=native)
                snapshot, = inspector.scan()
                self.assertFalse(snapshot.reliable)
                self.assertFalse(snapshot.visible)
                automation.descendants.assert_not_called()

    def test_uia_and_child_enumeration_failure_are_unreliable(self):
        for operation in ("descendants", "protected_children"):
            with self.subTest(operation=operation):
                inspector, native, automation = self.inspector()
                target = automation if operation == "descendants" else native
                getattr(target, operation).side_effect = OSError("Explorer restarted")
                snapshot, = inspector.scan()
                self.assertFalse(snapshot.reliable)
                self.assertFalse(snapshot.visible)

    def test_restart_during_top_level_enumeration_returns_empty_then_recovers(self):
        inspector, native, _ = self.inspector()
        native.taskbars.side_effect = [OSError("Restart"), (1,)]
        self.assertEqual(inspector.scan(), ())
        self.assertTrue(inspector.scan()[0].reliable)

    def test_multiple_taskbars_are_scanned(self):
        inspector, native, automation = self.inspector()
        native.taskbars.return_value = (1, 2)
        snapshots = inspector.scan()
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(automation.descendants.call_count, 2)

    def test_scan_and_com_cleanup_enforce_owner_thread(self):
        inspector, _, automation = self.inspector()
        errors = []

        def wrong_thread():
            for operation in (inspector.scan, inspector.close):
                try:
                    operation()
                except Exception as error:
                    errors.append(error)

        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(2)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("scanner thread" in str(error) for error in errors))
        automation.close.assert_not_called()
        inspector.close()
        inspector.close()
        automation.close.assert_called_once_with()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            inspector.scan()

    def test_native_and_com_are_not_imported_on_non_windows(self):
        with patch.object(occupancy.sys, "platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "Windows 10/11"):
                WindowsTaskbarInspector()


class NativeAdapterTests(unittest.TestCase):
    def native(self):
        native = occupancy._WindowsNative.__new__(occupancy._WindowsNative)
        native.user = Mock()
        native.shell = Mock()
        return native

    def test_enumerates_primary_and_secondary_taskbars_only(self):
        native = self.native()
        native._enumerate = Mock(return_value=((1, "shell_traywnd"), (2, "other"),
                                               (3, "shell_secondarytraywnd")))
        self.assertEqual(native.taskbars(), (1, 3))

    def test_protects_native_tray_clock_show_desktop_and_toolbar_children(self):
        native = self.native()
        names = ("traynotifywnd", "trayclockwclass", "trayshowdesktopbuttonwclass",
                 "toolbarwindow32", "searchbox", "startbutton", "rebarwindow32")
        native._enumerate = Mock(return_value=tuple(enumerate(names, 1)))
        native.is_visible = lambda hwnd: hwnd != 2
        native.bounds = lambda hwnd: Rect(hwnd * 10, 1040, hwnd * 10 + 10, 1080)
        rectangles = native.protected_children(99)
        self.assertEqual(rectangles, tuple(native.bounds(hwnd) for hwnd in (1, 3, 4, 5, 6)))

    def test_fullscreen_requires_covering_whole_monitor_and_ignores_desktop(self):
        native = self.native()
        native.user.GetForegroundWindow.return_value = 123
        native.is_visible = lambda hwnd: True
        native._class = lambda hwnd: "chrome_widgetwin_1"
        native.bounds = lambda hwnd: MONITOR
        self.assertTrue(native.fullscreen_covering(MONITOR))
        native.bounds = lambda hwnd: Rect(-8, -8, 1928, 1048)
        self.assertFalse(native.fullscreen_covering(MONITOR))
        native.bounds = lambda hwnd: MONITOR
        native._class = lambda hwnd: "workerw"
        self.assertFalse(native.fullscreen_covering(MONITOR))

    def test_native_windows_structures_use_windows_widths_on_any_host(self):
        self.assertEqual(ctypes.sizeof(occupancy._RECT), 16)
        self.assertEqual(ctypes.sizeof(occupancy._MONITORINFO), 40)


class AutomationAdapterTests(unittest.TestCase):
    def com_modules(self):
        comtypes = ModuleType("comtypes")
        comtypes.CoInitializeEx = Mock()
        comtypes.CoUninitialize = Mock()
        client = ModuleType("comtypes.client")
        comtypes.client = client
        generated = SimpleNamespace(CUIAutomation=object(), CUIAutomation8=object(),
                                    IUIAutomation=object(), IUIAutomation2=object())
        newer = SimpleNamespace(ConnectionTimeout=None, TransactionTimeout=None)
        automation = SimpleNamespace(QueryInterface=Mock(return_value=newer),
                                     CreateTrueCondition=Mock(return_value=object()))
        client.GetModule = Mock(return_value=generated)
        client.CreateObject = Mock(return_value=automation)
        return comtypes, client, generated, newer

    def test_com_startup_uses_mta_modern_provider_and_bounded_supported_timeouts(self):
        comtypes, client, generated, newer = self.com_modules()
        with patch.dict(sys.modules, {"comtypes": comtypes, "comtypes.client": client}), \
                patch.object(sys, "coinit_flags", 2, create=True):
            provider = occupancy._WindowsAutomation()
            self.assertEqual(sys.coinit_flags, 2)
            comtypes.CoInitializeEx.assert_called_once_with(0)
            client.GetModule.assert_called_once_with("UIAutomationCore.dll")
            client.CreateObject.assert_called_once_with(generated.CUIAutomation8,
                                                        interface=generated.IUIAutomation)
            self.assertEqual(newer.ConnectionTimeout, 250)
            self.assertEqual(newer.TransactionTimeout, 500)
            provider.close()
            comtypes.CoUninitialize.assert_called_once_with()

    def test_com_startup_error_releases_initialized_apartment_and_explains_failure(self):
        comtypes, client, _, _ = self.com_modules()
        client.GetModule.side_effect = OSError("No provider")
        with patch.dict(sys.modules, {"comtypes": comtypes, "comtypes.client": client}):
            with self.assertRaisesRegex(RuntimeError, "cannot safely locate empty space"):
                occupancy._WindowsAutomation()
        comtypes.CoUninitialize.assert_called_once_with()

    def provider(self, collection):
        provider = occupancy._WindowsAutomation.__new__(occupancy._WindowsAutomation)
        root = SimpleNamespace(CurrentIsOffscreen=False, FindAll=Mock(return_value=collection))
        provider._automation = SimpleNamespace(ElementFromHandle=Mock(return_value=root))
        provider._condition = object()
        return provider, root

    def test_actual_uia_properties_are_read_and_descendant_scope_used(self):
        element = SimpleNamespace(
            CurrentBoundingRectangle=SimpleNamespace(left=700, top=1040, right=748, bottom=1080),
            CurrentControlType=BUTTON, CurrentClassName="Taskbar.TaskListButton",
            CurrentAutomationId="app", CurrentIsOffscreen=False)
        collection = SimpleNamespace(Length=1, GetElement=Mock(return_value=element))
        provider, root = self.provider(collection)
        self.assertEqual(provider.descendants(1), (button(700, 748, class_name="Taskbar.TaskListButton", aid="app"),))
        root.FindAll.assert_called_once_with(4, provider._condition)

    def test_tree_limit_checked_before_get_element(self):
        collection = SimpleNamespace(Length=occupancy.MAX_UIA_ELEMENTS + 1, GetElement=Mock())
        provider, _ = self.provider(collection)
        with self.assertRaisesRegex(RuntimeError, "limit"):
            provider.descendants(1)
        collection.GetElement.assert_not_called()

    def test_scan_deadline_stops_slow_tree_before_reading_more_elements(self):
        collection = SimpleNamespace(Length=1, GetElement=Mock())
        provider, _ = self.provider(collection)
        with patch.object(occupancy.time, "monotonic", side_effect=(0.0, 0.8)):
            with self.assertRaisesRegex(RuntimeError, "freshness deadline"):
                provider.descendants(1)
        collection.GetElement.assert_not_called()

    def test_root_offscreen_and_property_failures_cannot_be_silently_skipped(self):
        collection = SimpleNamespace(Length=1, GetElement=Mock(side_effect=OSError("ElementNotAvailable")))
        provider, root = self.provider(collection)
        with self.assertRaises(OSError):
            provider.descendants(1)
        root.CurrentIsOffscreen = True
        with self.assertRaisesRegex(RuntimeError, "offscreen"):
            provider.descendants(1)

    def test_com_cleanup_balances_initializations_on_owner_thread(self):
        provider = occupancy._WindowsAutomation.__new__(occupancy._WindowsAutomation)
        provider._condition = object()
        provider._automation = object()
        provider._comtypes = SimpleNamespace(CoUninitialize=Mock())
        provider._initializations = 2
        provider.close()
        provider.close()
        self.assertEqual(provider._comtypes.CoUninitialize.call_count, 2)
        self.assertIsNone(provider._automation)


if __name__ == "__main__":
    unittest.main()
