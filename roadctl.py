#!/usr/bin/env python3
"""RoadMakerAgent: safe, file-based control plane for the native RoadMaker kernel.

This is intentionally NOT a GUI-click bot or a network daemon. It calls the
same C++ engine and edit-command factories used by the Qt editor. The exchange
boundary is ASAM OpenDRIVE (.xodr) and OpenSCENARIO (.xosc).
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = Path(os.environ.get("ROADMAKER_REPO", str(ROOT.parent))).resolve()
PROJECTS = ROOT / "projects"
sys.path.insert(0, str(ROOT / "vendor"))

import roadmaker as rm  # noqa: E402


PROFILES = {
    "default": rm.LaneProfile.two_lane_default,
    "rural": rm.LaneProfile.two_lane_rural,
    "urban": rm.LaneProfile.urban_sidewalk,
    "arterial": rm.LaneProfile.arterial,
    "highway": rm.LaneProfile.highway,
    "collector": rm.LaneProfile.collector,
    "local": rm.LaneProfile.local_road,
    "freeway": rm.LaneProfile.freeway,
}
CONTROLS = {
    "two_phase": rm.edit.SignalizeTemplate.TWO_PHASE,
    "protected_left": rm.edit.SignalizeTemplate.FOUR_WAY_PROTECTED_LEFT,
    "all_way_stop": rm.edit.SignalizeTemplate.ALL_WAY_STOP,
    "two_way_stop": rm.edit.SignalizeTemplate.TWO_WAY_STOP,
}
ACTORS = {
    "car": rm.osc.ActorKind.Car,
    "truck": rm.osc.ActorKind.Truck,
    "bus": rm.osc.ActorKind.Bus,
    "motorbike": rm.osc.ActorKind.Motorbike,
    "bicycle": rm.osc.ActorKind.Bicycle,
    "pedestrian": rm.osc.ActorKind.Pedestrian,
}
OBJECT_TYPES = {key.lower(): getattr(rm.ObjectType, key) for key in (
    "TREE", "VEGETATION", "POLE", "BARRIER", "BUILDING", "OBSTACLE", "OTHER")}
EDITOR = REPO / "build" / "dev-windows" / "editor" / "roadmaker-editor.exe"


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def export_raised_glb(network, output: Path) -> None:
    """Export with the same 3-D sidewalk mode used by PrawkoRoadStudio GUI."""
    options = rm.MeshOptions()
    if not hasattr(options, "raised_sidewalks"):
        raise RuntimeError("STALE_AGENT_BINDING: rebuild editor/python to enable 3-D curbs")
    options.raised_sidewalks = True
    rm.export_glb(rm.build_network_mesh(network, options), str(output))


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def editor_windows() -> list[str]:
    """Read-only Windows title enumeration; conservatively protect open scenes."""
    if os.name != "nt":
        return []
    titles = []
    callback_t = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(hwnd, unused):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
                if "RoadMaker" in buf.value:
                    titles.append(buf.value)
        return True

    callback = callback_t(collect)
    ctypes.windll.user32.EnumWindows(callback, 0)
    return titles


def in_editor(path: Path) -> list[str]:
    return [title for title in editor_windows() if path.name.casefold() in title.casefold()]


def enforce_output(path: Path, force: bool) -> None:
    path = path.resolve()
    if path.suffix.lower() != ".xodr":
        raise ValueError("Output must end with .xodr")
    if not path.is_relative_to(PROJECTS.resolve()):
        raise ValueError(f"Outputs are restricted to {PROJECTS}; copy source into projects first")
    if path.exists():
        if in_editor(path):
            raise ValueError(f"Refusing to overwrite scene open in RoadMaker: {in_editor(path)}")
        if not force:
            raise FileExistsError(f"{path} exists; choose another name or explicitly pass --force")


def report_diagnostics(entries) -> list[dict]:
    return [
        {
            "severity": str(entry.severity),
            "location": str(entry.location),
            "message": entry.message,
            "rule_id": entry.rule_id,
        } for entry in entries
    ]


def no_errors(entries, source: str) -> None:
    errors = [str(f.message) for f in entries if f.severity == rm.Severity.ERROR]
    if errors:
        raise ValueError(f"{source} validation failed: {errors[:12]}")


def load_network(path: Path):
    if not path.is_file():
        raise FileNotFoundError(path)
    network, parsed = rm.load_xodr(str(path))
    no_errors(parsed, "OpenDRIVE parser")
    return network, list(parsed)


def find_road(network, odr_id):
    road = network.find_road(str(odr_id))
    if network.road(road) is None:
        raise ValueError(f"Unknown OpenDRIVE road id {odr_id}")
    return road


def stack_apply(network, commands: list[dict]) -> rm.edit.EditStack:
    stack = rm.edit.EditStack()
    for item in commands:
        op = item["op"]
        if op == "add_object":
            add_objects(network, stack, [item])
            continue
        if op == "add_sign":
            add_signs(network, stack, [item])
            continue
        if op in ("set_junction_default_corner_radius", "set_corner_radius"):
            jid = network.find_junction(str(item["junction"]))
            if network.junction(jid) is None:
                raise ValueError(f"Unknown junction {item['junction']}")
            if op == "set_junction_default_corner_radius":
                cmd = rm.edit.set_junction_default_corner_radius(network, jid, float(item["radius_m"]))
            else:
                corners = rm.junction_corners(network, jid)
                index = int(item["corner_index"])
                if index < 0 or index >= len(corners):
                    raise ValueError(f"Invalid corner_index {index} (have {len(corners)})")
                corner = corners[index]
                cmd = rm.edit.set_corner_radius(network, jid, corner.arm_a, corner.arm_b,
                                                float(item["radius_m"]))
        elif op == "create_road":
            pts = [tuple(map(float, p)) for p in item["points"]]
            if len(pts) < 2:
                raise ValueError("create_road needs >=2 points")
            cmd = rm.edit.create_road(pts, PROFILES[item.get("profile", "rural")](),
                                      item.get("name", ""))
        else:
            rid = find_road(network, item["road"])
            if op == "move_waypoint":
                cmd = rm.edit.move_waypoint(network, rid, int(item["index"]),
                                             tuple(map(float, item["point"])))
            elif op == "translate_road":
                cmd = rm.edit.translate_road(network, rid, float(item["dx"]), float(item["dy"]))
            elif op == "rename_road":
                cmd = rm.edit.rename_road(network, rid, str(item["name"]))
            elif op == "set_node_elevation":
                cmd = rm.edit.set_node_elevation(network, rid, int(item["index"]), float(item["height_m"]))
            elif op == "set_lane_width":
                road = network.road(rid)
                section = network.lane_section(road.sections[0])
                lid = next((x for x in section.lanes
                            if network.lane(x).odr_id == int(item["lane"])), None)
                if lid is None:
                    raise ValueError(f"Road {item['road']} has no lane {item['lane']}")
                cmd = rm.edit.set_lane_width(network, lid, float(item["width_m"]))
            elif op in ("move_object", "set_object_model"):
                oid = next((o for o in network.objects_of(rid)
                            if network.object(o).odr_id == str(item["id"])), None)
                if oid is None:
                    raise ValueError(f"Road {item['road']} has no object {item['id']}")
                if op == "move_object":
                    cmd = rm.edit.move_object(network, oid, float(item["s"]), float(item["t"]))
                else:
                    cmd = rm.edit.set_object_model(network, oid, str(item["model"]))
            elif op in ("move_signal", "set_signal_text", "set_signal_value"):
                sid = next((s for s in network.signals_of(rid)
                            if network.signal(s).odr_id == str(item["id"])), None)
                if sid is None:
                    raise ValueError(f"Road {item['road']} has no signal {item['id']}")
                if op == "move_signal":
                    cmd = rm.edit.move_signal(network, sid, float(item["s"]), float(item["t"]))
                elif op == "set_signal_text":
                    cmd = rm.edit.set_signal_text(network, sid, str(item["text"]))
                else:
                    cmd = rm.edit.set_signal_value(network, sid, float(item["value"]), str(item["unit"]))
            else:
                raise ValueError(f"Unsupported operation: {op}")
        stack.push(network, cmd)
    return stack


def add_road(network, stack, item: dict) -> None:
    pts = [tuple(map(float, p)) for p in item["points"]]
    if len(pts) < 2:
        raise ValueError("Road needs at least two 2-D waypoints")
    profile = PROFILES[item.get("profile", "rural")]()
    stack.push(network, rm.edit.create_road(pts, profile, item.get("name", "")))


def add_assembly(network, stack, item: dict) -> None:
    params = rm.edit.assembly.IntersectionParams()
    params.arm_length_m = float(item.get("arm_length_m", 70))
    params.gap_m = float(item.get("gap_m", 0))
    params.profile = PROFILES[item.get("profile", "urban")]()
    pose = item.get("pose", [0, 0, 0])
    where = rm.edit.assembly.Pose(float(pose[0]), float(pose[1]),
                                  float(pose[2]) * 3.141592653589793 / 180)
    kind = item.get("kind", "x")
    factory = {"x": rm.edit.assembly.x_intersection,
               "t": rm.edit.assembly.t_intersection}.get(kind)
    if factory is None:
        raise ValueError(f"Unknown assembly {kind} (supported: x, t)")
    stack.push(network, factory(network, where, params))


def add_custom_junction(network, stack, item: dict) -> None:
    ends = [
        rm.RoadEnd(find_road(network, entry["road"]),
                   rm.ContactPoint.END if entry.get("end", "end") == "end"
                   else rm.ContactPoint.START)
        for entry in item["ends"]
    ]
    stack.push(network, rm.edit.create_junction(network, ends))


def add_features(network, stack, features: dict) -> None:
    if not features:
        return
    for jid in network.junction_ids:
        if features.get("crosswalks"):
            for rid, obj in rm.edit.junction_crosswalks(network, jid):
                stack.push(network, rm.edit.add_object(network, rid, obj))
        if features.get("lane_arrows"):
            for rid, obj in rm.edit.junction_lane_arrows(network, jid):
                stack.push(network, rm.edit.add_object(network, rid, obj))
        if features.get("center_marks"):
            for lane, mark in rm.edit.junction_center_marks(network, jid):
                stack.push(network, rm.edit.set_road_mark(network, lane, mark))
        if features.get("control"):
            opt = rm.edit.SignalizeOptions()
            opt.tmpl = CONTROLS[features["control"]]
            stack.push(network, rm.edit.signalize_junction(network, jid, opt))


def add_objects(network, stack, objects: list[dict]) -> None:
    for idx, item in enumerate(objects):
        road = find_road(network, item["road"])
        obj = rm.Object()
        obj.odr_id = str(item.get("id", f"agent_obj_{idx + 1}"))
        obj.name = str(item.get("model", "tree_oak"))
        obj.type = OBJECT_TYPES[item.get("type", "tree").lower()]
        obj.s, obj.t = float(item["s"]), float(item["t"])
        obj.height = float(item.get("height_m", 4.5))
        obj.radius = float(item.get("radius_m", 1.2))
        stack.push(network, rm.edit.add_object(network, road, obj))


def add_signs(network, stack, signs: list[dict]) -> None:
    for idx, item in enumerate(signs):
        road = find_road(network, item["road"])
        sign = rm.Signal()
        sign.odr_id = str(item.get("id", f"agent_signal_{idx + 1}"))
        sign.type = str(item["type"])
        sign.subtype = str(item.get("subtype", "-1"))
        sign.country = str(item.get("country", "OpenDRIVE"))
        sign.dynamic = bool(item.get("dynamic", False))
        sign.s, sign.t = float(item["s"]), float(item.get("t", -5.5))
        if "width_m" in item:
            sign.width = float(item["width_m"])
        if "height_m" in item:
            sign.height = float(item["height_m"])
        if "value" in item:
            sign.value = float(item["value"])
            sign.unit = str(item["unit"])
        stack.push(network, rm.edit.add_signal(network, road, sign))
        new_id = next(
            i for i in network.signals_of(road)
            if network.signal(i).odr_id == sign.odr_id)
        stack.push(network, rm.edit.auto_orient_signal(network, new_id))


def lane_position(item: dict):
    pos = rm.osc.LanePosition()
    pos.road_id = str(item["road"])
    pos.lane_id = str(item.get("lane", "-1"))
    pos.s = float(item["s"])
    pos.offset = float(item.get("offset_m", 0))
    return pos


def add_maneuvers(scene, stack, maneuvers: list[dict], actor_names: set[str]) -> None:
    """Author behavioral events via the same ScenarioStack used by the GUI.

    Supported: time-triggered or proximity-triggered lane changes. The target
    lane is relative to the ACTOR, so it remains road-anchored.
    """
    if not maneuvers:
        return
    groups = []
    for index, config in enumerate(maneuvers, 1):
        kind = config.get("kind")
        if kind != "lane_change":
            raise ValueError(f"Unknown maneuver kind: {kind}")
        name = str(config["actor"])
        if name not in actor_names:
            raise ValueError(f"Maneuver actor is not present in scenario: {name}")
        shift = int(config["relative_lane"])
        if shift == 0:
            raise ValueError("A lane change requires a nonzero relative_lane")
        change = rm.osc.LaneChangeAction()
        dyn = change.dynamics
        dyn.dynamics_shape = "linear"
        dyn.dynamics_dimension = "time"
        dyn.value = float(config.get("duration_s", 2))
        change.dynamics = dyn
        target = rm.osc.RelativeTargetLane()
        target.entity_ref = name
        target.value = shift
        change.target = target
        lateral = rm.osc.LateralAction()
        lateral.lane_change = change
        private = rm.osc.PrivateAction()
        private.lateral = lateral
        action = rm.osc.Action()
        action.name = f"action_{index}"
        action.action = private

        trigger_spec = config["trigger"]
        cond = rm.osc.Condition()
        cond.name = f"when_{index}"
        if "time_s" in trigger_spec:
            timing = rm.osc.SimulationTimeCondition()
            timing.value = float(trigger_spec["time_s"])
            timing.rule = "greaterThan"
            cond.simulation_time = timing
        elif "distance_to" in trigger_spec:
            other = str(trigger_spec["distance_to"])
            if other not in actor_names or other == name:
                raise ValueError(f"Invalid distance_to actor: {other}")
            relative = rm.osc.RelativeDistanceCondition()
            relative.entity_ref = other
            relative.freespace = True
            relative.relative_distance_type = "longitudinal"
            relative.rule = "lessThan"
            relative.value = float(trigger_spec.get("distance_m", 12))
            triggering = rm.osc.TriggeringEntities()
            entity = rm.osc.EntityRef()
            entity.entity_ref = name
            triggering.entity_refs = [entity]
            by_entity = rm.osc.ByEntityCondition()
            by_entity.triggering_entities = triggering
            by_entity.entity_condition = relative
            cond.by_entity = by_entity
        else:
            raise ValueError("Trigger requires time_s or distance_to")
        condition_group = rm.osc.ConditionGroup()
        condition_group.conditions = [cond]
        trigger = rm.osc.Trigger()
        trigger.condition_groups = [condition_group]
        event = rm.osc.Event()
        event.name = f"event_{index}"
        event.actions = [action]
        event.start_trigger = trigger
        maneuver = rm.osc.StoryManeuver()
        maneuver.name = f"maneuver_{index}"
        maneuver.events = [event]
        ref = rm.osc.EntityRef()
        ref.entity_ref = name
        group = rm.osc.ManeuverGroup()
        group.name = f"group_{index}"
        group.actors = [ref]
        group.maneuvers = [maneuver]
        groups.append(group)
    act = rm.osc.Act()
    act.name = "agent_act"
    act.maneuver_groups = groups
    story = rm.osc.Story()
    story.name = "agent_story"
    story.acts = [act]
    stack.push(scene, rm.osc.edit.set_story(scene, 0, story))


def build_scenario(network, config: dict, logic_name: str):
    scene = rm.osc.Scenario()
    stack = rm.osc.edit.ScenarioStack()
    header = scene.header
    header.description = str(config.get("description", "RoadMakerAgent scenario"))
    scene.header = header
    stack.push(scene, rm.osc.edit.set_logic_file(scene, logic_name))
    if config.get("sync_signals", True):
        for jid in network.junction_ids:
            if network.junction(jid).junction_controllers:
                stack.push(scene, rm.osc.edit.sync_traffic_signals(scene, network, jid))

    for actor in config.get("actors", []):
        name = str(actor["name"])
        stack.push(scene, rm.osc.edit.place_scenario_object(
            scene, rm.osc.make_actor(ACTORS[actor.get("kind", "car")], name),
            lane_position(actor)))
        if "speed_kmh" in actor:
            stack.push(scene, rm.osc.edit.set_entity_init_speed(
                scene, name, float(actor["speed_kmh"]) / 3.6))
        if actor.get("route"):
            route = rm.osc.Route()
            route.name = f"{name}_route"
            waypoints = []
            for item in actor["route"]:
                waypoint = rm.osc.RouteWaypoint()
                waypoint.route_strategy = "shortest"
                waypoint.position = lane_position(item)
                waypoints.append(waypoint)
            route.waypoints = waypoints
            stack.push(scene, rm.osc.edit.assign_route(scene, name, route))

    add_maneuvers(scene, stack, config.get("maneuvers", []),
                  {str(a["name"]) for a in config.get("actors", [])})

    if config.get("stop_after_s") is not None:
        condition = rm.osc.Condition()
        condition.name = "scenario_timeout"
        timer = rm.osc.SimulationTimeCondition()
        timer.value = float(config["stop_after_s"])
        timer.rule = "greaterThan"
        condition.simulation_time = timer
        group = rm.osc.ConditionGroup()
        group.conditions = [condition]
        trigger = rm.osc.Trigger()
        trigger.condition_groups = [group]
        stack.push(scene, rm.osc.edit.set_stop_trigger(scene, trigger))

    no_errors(rm.osc.validate_scenario(scene), "OpenSCENARIO")
    no_errors(rm.osc.validate_scenario_against_network(scene, network),
              "OpenSCENARIO cross-document")
    no_errors(rm.osc.validate_routes(network, scene), "OpenSCENARIO routes")
    return scene


def validate(network, parsed=None, scenario=None):
    found = list(parsed or []) + list(rm.validate_network(network))
    if scenario is not None:
        found += list(rm.osc.validate_scenario(scenario))
        found += list(rm.osc.validate_scenario_against_network(scenario, network))
        found += list(rm.osc.validate_routes(network, scenario))
    return found


def describe(network, path: Path, parsed=None, scenario=None) -> dict:
    roads = []
    for rid in network.road_ids:
        road = network.road(rid)
        lanes = []
        for sid in road.sections:
            section = network.lane_section(sid)
            lanes.extend(network.lane(l).odr_id for l in section.lanes)
        roads.append({
            "id": road.odr_id, "name": road.name, "length_m": round(road.length, 3),
            "geometry_records": road.plan_view.record_count, "lane_ids": sorted(set(lanes))})
    findings = validate(network, parsed, scenario)
    output = {
        "path": str(path), "sha256": sha(path) if path.exists() else None,
        "roads": roads,
        "junction_ids": [network.junction(j).odr_id for j in network.junction_ids],
        "objects": network.object_count, "signals": network.signal_count,
        "controllers": network.controller_count,
        "findings": report_diagnostics(findings),
        "errors": sum(f.severity == rm.Severity.ERROR for f in findings),
        "open_editor_windows": in_editor(path),
    }
    if scenario is not None:
        output["scenario_actors"] = [a.name for a in scenario.entities.scenario_objects]
    return output


def save_output(network, out: Path, scenario=None, name="RoadMakerAgent",
                force=False, expect_sha=None):
    out.parent.mkdir(parents=True, exist_ok=True)
    enforce_output(out, force)
    if expect_sha and (not out.exists() or sha(out) != expect_sha):
        raise ValueError(f"SHA256 conflict: {out} was modified since the last read")

    errors = validate(network, scenario=scenario)
    no_errors(errors, "pre-save")

    # Pre-stage and validate, then replace. No program-owned source file is edited.
    fd, staging = tempfile.mkstemp(prefix=".rm_agent_", suffix=".xodr", dir=out.parent)
    os.close(fd)
    staging = Path(staging)
    scenario_tmp = None
    try:
        rm.save_xodr(network, str(staging), name)
        reparsed, parse_issues = load_network(staging)
        no_errors(validate(reparsed, parse_issues), "saved OpenDRIVE")
        if scenario is not None:
            fd, p = tempfile.mkstemp(prefix=".rm_agent_", suffix=".xosc", dir=out.parent)
            os.close(fd)
            scenario_tmp = Path(p)
            rm.osc.save_xosc(scenario, str(scenario_tmp))

        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        for dest in (out, out.with_suffix(".xosc") if scenario is not None else None):
            if dest is not None and dest.exists():
                if in_editor(out):
                    raise ValueError("Editor has the target scene open; aborting overwrite")
                if not force:
                    raise FileExistsError(dest)
                shutil.copy2(dest, dest.with_name(dest.name + f".backup-{suffix}"))
        os.replace(staging, out)
        if scenario_tmp:
            os.replace(scenario_tmp, out.with_suffix(".xosc"))
            scenario_tmp = None
    finally:
        staging.unlink(missing_ok=True)
        if scenario_tmp:
            scenario_tmp.unlink(missing_ok=True)

    manifest = out.parent / "project.json"
    if not manifest.exists():
        data = {"project_version": 2, "name": name, "last_scene": out.name}
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return describe(network, out, scenario=scenario)


def create_scene(spec: dict):
    supported = {"name", "roads", "intersections", "junctions", "features", "objects", "signs", "scenario"}
    unknown = set(spec) - supported
    if unknown:
        raise ValueError(f"Unsupported scene fields: {sorted(unknown)}")
    if not any(spec.get(group) for group in ("roads", "intersections", "junctions")):
        raise ValueError("Scene requires roads, intersections or junctions")
    network = rm.RoadNetwork()
    stack = rm.edit.EditStack()
    for item in spec.get("roads", []):
        add_road(network, stack, item)
    for item in spec.get("intersections", []):
        add_assembly(network, stack, item)
    for item in spec.get("junctions", []):
        add_custom_junction(network, stack, item)
    add_features(network, stack, spec.get("features", {}))
    add_objects(network, stack, spec.get("objects", []))
    add_signs(network, stack, spec.get("signs", []))
    return network, stack


def change_scene(source: Path, spec: dict):
    network, parse_issues = load_network(source)
    stack_apply(network, spec["operations"])
    no_errors(validate(network, parse_issues), "edited OpenDRIVE")
    return network


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("scene", type=Path)
    validate_cmd = commands.add_parser("validate")
    validate_cmd.add_argument("scene", type=Path)
    build = commands.add_parser("build")
    build.add_argument("--spec", required=True, type=Path)
    build.add_argument("--out", required=True, type=Path)
    build.add_argument("--force", action="store_true")
    build.add_argument("--open", action="store_true")
    edit = commands.add_parser("edit")
    edit.add_argument("scene", type=Path)
    edit.add_argument("--spec", required=True, type=Path)
    edit.add_argument("--out", required=True, type=Path)
    edit.add_argument("--force", action="store_true")
    edit.add_argument("--expect-sha256")
    export = commands.add_parser("export")
    export.add_argument("scene", type=Path)
    export.add_argument("--out", required=True, type=Path)
    opener = commands.add_parser("open")
    opener.add_argument("scene", type=Path)
    args = parser.parse_args()

    if args.command == "status":
        emit({"roadmaker_version": rm.version(), "repository": str(REPO),
              "native_editor_present": EDITOR.is_file(), "native_editor_windows": editor_windows(),
              "agent_root": str(ROOT), "project_root": str(PROJECTS)})
        return
    if args.command == "open":
        path = args.scene.resolve(strict=True)
        if not EDITOR.is_file():
            raise FileNotFoundError(EDITOR)
        process = subprocess.Popen([str(EDITOR), str(path)], cwd=str(REPO))
        emit({"started_pid": process.pid, "scene": str(path)})
        return

    if args.command == "build":
        spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
        network, stack = create_scene(spec)
        output = args.out.resolve()
        scene = build_scenario(network, spec["scenario"], output.name) if "scenario" in spec else None
        result = save_output(network, output, scene, name=spec.get("name", output.stem),
                             force=args.force)
        result["commands_executed"] = stack.size
        if args.open:
            process = subprocess.Popen([str(EDITOR), str(output)], cwd=str(REPO))
            result["opened_pid"] = process.pid
        emit(result)
        return

    if args.command == "edit":
        spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
        source = args.scene.resolve(strict=True)
        output = args.out.resolve()
        if any(w.casefold().startswith(source.name.casefold() + "*") for w in in_editor(source)):
            raise ValueError("Source scene has unsaved edits in GUI; save in RoadMaker first")
        network = change_scene(source, spec)
        scenario = None
        previous = source.with_suffix(".xosc")
        if previous.is_file():
            read = rm.osc.load_xosc(str(previous))
            no_errors(read.diagnostics, "OpenSCENARIO input")
            scenario = read.scenario
            if source.name != output.name:
                stack = rm.osc.edit.ScenarioStack()
                stack.push(scenario, rm.osc.edit.set_logic_file(scenario, output.name))
        emit(save_output(network, output, scenario, name=spec.get("name", output.stem),
                         force=args.force, expect_sha=args.expect_sha256))
        return

    source = args.scene.resolve(strict=True)
    network, issues = load_network(source)
    scenario = None
    xosc = source.with_suffix(".xosc")
    if xosc.exists():
        read = rm.osc.load_xosc(str(xosc))
        no_errors(read.diagnostics, "OpenSCENARIO parser")
        scenario = read.scenario
    if args.command in ("validate", "inspect"):
        out = describe(network, source, issues, scenario)
        emit(out)
        if args.command == "validate" and out["errors"]:
            raise SystemExit(2)
    elif args.command == "export":
        output = args.out.resolve()
        if output.exists():
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        export_raised_glb(network, output)
        emit({"mesh": str(output), "bytes": output.stat().st_size,
              "source_sha256": sha(source)})


if __name__ == "__main__":
    try:
        run()
    except (ValueError, OSError, KeyError, TypeError, IndexError, RuntimeError) as exc:
        emit({"ok": False, "error": str(exc), "type": type(exc).__name__})
        raise SystemExit(1)
