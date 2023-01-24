

import re
import sys
from pathlib import Path
import json
import anytree

def repopath(path):
    return Path(__file__).parents[2] / path


class TaskConfig:
    def __init__(self, path):
        self._filepath = path.resolve()
        self._data = json.loads(self._filepath.read_text())
        self.name = self._data["name"]

    def _resolve_inheritance(self, tasks):
        extends = self._data.get("extends", [])
        if not isinstance(extends, list):
            extends = [extends]
        for extend in extends:
            if (task := tasks.get(extend)):
                task.parent = self
            else:
                raise ValueError(f"Cannot find task '{extend}' to inherit from!")


task_configs = {}

# for task_file in repopath(".").glob("**/*task.json"):
#     task = TaskConfig(task_file)
#     task_configs[task.name] = task

# for task in task_configs.values():
#     task._resolve_inheritance(task_configs)

# for task in task_configs.values():
#     print(anytree.RenderTree(task, style=anytree.ContRoundStyle()))

# exit(1)


sensors = {}
for task_file in repopath(".").glob("**/*task.json"):
    print("Loading", task_file.relative_to(Path().cwd()))
    data = json.loads(task_file.read_text())
    sensors[data["name"]] = data

board_file = Path(sys.argv[1])
print("Loading", board_file)
board_data = json.loads(board_file.read_text())
# print(board_data)

task_starts = []

for iname, interface in board_data["interfaces"].items():
    for task in interface["tasks"]:
        sensor = sensors.get(task["task"])
        start_options = sensor["commands"]["start"].get("options")
        arguments = []
        for oname, ovalue in task["options"].items():
            otype = start_options[oname]["type"]
            nsh = start_options[oname]['nsh']
            if otype == "bool":
                if bool(ovalue):
                    arguments.append(f"-{nsh}")
            elif otype == "int":
                arguments.append(f"-{nsh} {int(ovalue)}")
        task_starts.append((task["task"], " ".join(arguments)))


print()
for task, args in task_starts:
    print(f"{task} {args} start")

