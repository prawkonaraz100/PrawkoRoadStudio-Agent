"""Regression tests for RoadMakerAgent's file-based Python/C++ bridge."""
from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

import roadctl as ctl


class RoadMakerAgentTests(unittest.TestCase):
    def setUp(self):
        ctl.PROJECTS.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="agent-test-", dir=ctl.PROJECTS)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_export_uses_current_raised_curbs_and_glb_uvs(self):
        """Catch the stale .pyd that silently flattened every exported junction."""
        source = ctl.REPO / "assets" / "samples" / "gs1_urban_intersection.xodr"
        network, _ = ctl.load_network(source)
        out = self.root / "raised-curbs.glb"
        ctl.export_raised_glb(network, out)
        blob = out.read_bytes()
        magic, version, total = struct.unpack_from("<4sII", blob)
        self.assertEqual(magic, b"glTF")
        self.assertEqual(version, 2)
        self.assertEqual(total, len(blob))
        chunk_size, chunk_type = struct.unpack_from("<II", blob, 12)
        self.assertEqual(chunk_type, 0x4E4F534A)
        gltf = json.loads(blob[20:20 + chunk_size])
        bevels = [m for m in gltf["meshes"] if "corner bevel" in m.get("name", "")]
        self.assertGreaterEqual(len(bevels), 4, "raised sidewalk corners missing from agent GLB")
        for mesh in bevels:
            for primitive in mesh["primitives"]:
                for key in ("POSITION", "NORMAL", "TEXCOORD_0"):
                    self.assertIn(key, primitive["attributes"], mesh["name"])
                accessor = gltf["accessors"][primitive["attributes"]["TEXCOORD_0"]]
                self.assertEqual(accessor["type"], "VEC2")
                self.assertGreater(accessor["count"], 0)

        # Verify the opt-in curved render seam is really used by agent GLB.
        # The default raised kernel path remains byte-compatible with legacy
        # topology; the richer GLB must contain more actual corner triangles.
        baseline = ctl.rm.MeshOptions()
        baseline.raised_sidewalks = True
        self.assertFalse(baseline.curb_render_lod)
        base_file = self.root / "base-curbs.glb"
        ctl.rm.export_glb(ctl.rm.build_network_mesh(network, baseline), str(base_file))
        base_blob = base_file.read_bytes()
        base_size, _ = struct.unpack_from("<II", base_blob, 12)
        base_gltf = json.loads(base_blob[20:20 + base_size])
        def bevel_triangles(model):
            return sum(
                model["accessors"][p["indices"]]["count"] // 3
                for m in model["meshes"] if "corner bevel" in m.get("name", "")
                for p in m["primitives"]
            )
        self.assertGreater(bevel_triangles(gltf), bevel_triangles(base_gltf),
                           "Agent GLB skipped the safe curb visual LOD")

    def test_x_intersection_with_scenario_and_roundtrip(self):
        spec = {
            "intersections": [{"kind": "x", "gap_m": 24, "arm_length_m": 80,
                               "profile": "urban"}],
            "features": {"control": "two_phase", "crosswalks": True},
            "scenario": {"actors": [
                {"name": "Ego", "kind": "car", "road": "1", "lane": "-1",
                 "s": 20, "speed_kmh": 35}], "stop_after_s": 20},
        }
        network, stack = ctl.create_scene(spec)
        self.assertGreater(stack.size, 0)
        scenario = ctl.build_scenario(network, spec["scenario"], "intersection.xodr")
        out = self.root / "intersection.xodr"
        result = ctl.save_output(network, out, scenario, "Agent test")
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["scenario_actors"], ["Ego"])
        self.assertTrue(out.with_suffix(".xosc").exists())
        reloaded, issues = ctl.load_network(out)
        self.assertEqual(reloaded.junction_count, 1)
        imported = ctl.rm.osc.load_xosc(str(out.with_suffix(".xosc")))
        self.assertEqual(ctl.describe(reloaded, out, issues, imported.scenario)["errors"], 0)
        self.assertEqual(ctl.rm.write_xodr(reloaded), ctl.rm.write_xodr(
            ctl.rm.parse_xodr(ctl.rm.write_xodr(reloaded))[0]))

    def test_t_intersection_stops_and_manual_custom_junction(self):
        simple, _ = ctl.create_scene({
            "intersections": [{"kind": "t", "gap_m": 16, "profile": "rural"}],
            "features": {"control": "two_way_stop"},
        })
        self.assertEqual(simple.junction_count, 1)
        self.assertFalse(ctl.validate(simple))
        custom, _ = ctl.create_scene({
            "roads": [
                {"points": [[-40, 0], [-6, 0]]},
                {"points": [[40, 0], [6, 0]]},
                {"points": [[0, -40], [0, -6]]},
            ],
            "junctions": [{"ends": [{"road": str(i)} for i in (1, 2, 3)]}],
        })
        self.assertEqual(custom.junction_count, 1)
        self.assertFalse(ctl.validate(custom))

    def test_road_edits_and_collision_guards(self):
        road, _ = ctl.create_scene({
            "roads": [{"name": "S bend", "points": [[0, 0], [55, 8], [100, 30]],
                       "profile": "rural"}],
        })
        before_id = road.road(road.road_ids[0]).odr_id
        out = self.root / "curve.xodr"
        ctl.save_output(road, out)
        digest = ctl.sha(out)
        with self.assertRaises(FileExistsError):
            ctl.save_output(road, out)
        with self.assertRaisesRegex(ValueError, "SHA256 conflict"):
            ctl.save_output(road, out, force=True, expect_sha="0" * 64)
        self.assertEqual(ctl.sha(out), digest)
        edited = ctl.change_scene(out, {"operations": [
            {"op": "move_waypoint", "road": before_id, "index": 1, "point": [55, 22]},
            {"op": "rename_road", "road": before_id, "name": "Revised bend"},
        ]})
        new_path = self.root / "curve_v2.xodr"
        result = ctl.save_output(edited, new_path)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["roads"][0]["id"], before_id)
        self.assertEqual(result["roads"][0]["name"], "Revised bend")
        self.assertEqual(ctl.sha(out), digest)
        ctl.save_output(edited, new_path, force=True, expect_sha=ctl.sha(new_path))
        self.assertTrue(list(self.root.glob("curve_v2.xodr.backup-*")))

    def test_s_curve_with_lane_anchored_route(self):
        spec = {
            "roads": [{"points": [[0, 0], [55, 8], [100, 30], [170, 70]]}],
            "scenario": {"actors": [{
                "name": "RouteCar", "kind": "car", "road": "1", "lane": "-1",
                "s": 10, "speed_kmh": 40,
                "route": [
                    {"road": "1", "lane": "-1", "s": 10},
                    {"road": "1", "lane": "-1", "s": 155},
                ],
            }], "stop_after_s": 25},
        }
        road, _ = ctl.create_scene(spec)
        scenario = ctl.build_scenario(road, spec["scenario"], "curve.xodr")
        self.assertFalse(ctl.rm.osc.validate_routes(road, scenario))
        path = self.root / "curve.xodr"
        result = ctl.save_output(road, path, scenario)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["scenario_actors"], ["RouteCar"])

    def test_proximity_and_timed_lane_changes(self):
        network, _ = ctl.create_scene({
            "roads": [{"profile": "arterial",
                       "points": [[0, 0], [220, 0]]}],
        })
        base = {
            "actors": [
                {"name": "Ego", "kind": "car", "road": "1", "lane": "-1", "s": 30},
                {"name": "Target", "kind": "car", "road": "1", "lane": "-2", "s": 16},
            ],
            "stop_after_s": 30,
        }
        for trigger in ({"distance_to": "Ego", "distance_m": 15},
                        {"time_s": 4}):
            conf = dict(base)
            conf["maneuvers"] = [{"kind": "lane_change", "actor": "Target",
                                  "relative_lane": 1, "duration_s": 2,
                                  "trigger": trigger}]
            scenario = ctl.build_scenario(network, conf, "lane_change.xodr")
            text = ctl.rm.osc.write_xosc(scenario)
            self.assertIn("<LaneChangeAction>", text)
            self.assertIn("<Story name=", text)
            self.assertEqual(len(ctl.rm.osc.validate_scenario(scenario)), 0)

    def test_objects_signs_and_corner_radius_editing(self):
        network, _ = ctl.create_scene({
            "intersections": [{"kind": "x", "gap_m": 24, "profile": "urban"}],
            "objects": [{"road": "1", "id": "oak", "s": 18, "t": 8.5,
                         "model": "tree_oak", "type": "tree"}],
            "signs": [{"road": "1", "id": "stop", "s": 20, "t": -6.5,
                       "country": "US", "type": "R1-1"}],
        })
        self.assertEqual(network.junction_count, 1)
        operations = [
            {"op": "move_object", "road": "1", "id": "oak", "s": 24, "t": 8.5},
            {"op": "set_object_model", "road": "1", "id": "oak", "model": "shrub"},
            {"op": "move_signal", "road": "1", "id": "stop", "s": 26, "t": -6.5},
            {"op": "set_junction_default_corner_radius", "junction": "1", "radius_m": 5},
            {"op": "set_corner_radius", "junction": "1", "corner_index": 0, "radius_m": 4},
            {"op": "add_object", "road": "1", "id": "oak2", "s": 10, "t": 8.5,
             "model": "tree_oak", "type": "tree"},
        ]
        stack = ctl.stack_apply(network, operations)
        self.assertEqual(stack.size, len(operations))
        self.assertFalse(ctl.validate(network))
        jid = network.junction_ids[0]
        self.assertTrue(ctl.rm.junction_corners(network, jid)[0].radius_authored)
        out = self.root / "corner_test.xodr"
        result = ctl.save_output(network, out)
        self.assertEqual(result["errors"], 0)
        loaded, issues = ctl.load_network(out)
        self.assertEqual(loaded.junction(loaded.junction_ids[0]).default_corner_radius, 5)
        self.assertEqual(ctl.describe(loaded, out, issues)["errors"], 0)


if __name__ == "__main__":
    unittest.main()
