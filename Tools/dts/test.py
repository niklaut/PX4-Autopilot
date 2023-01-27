from pathlib import Path
def repopath(p):
    return (Path(__file__).parents[2] / p).resolve()
def zephyrpath(p):
    return (repopath("../zephyr") / p).resolve()

import sys
sys.path.append(str(zephyrpath("scripts/dts/python-devicetree/src")))

import os
import re
import jinja2
import shutil
import logging
import argparse
import tempfile
from devicetree import edtlib

"""
This code is taken and adapted from
 - zephyr/scripts/dts/gen_defines.py
 - zephyr/cmake/modules/dts.cmake
"""




def main():
    args = parse_args()
    setup_edtlib_logging()

    vendor_prefixes = {}
    for prefixes_file in args.vendor_prefixes:
        vendor_prefixes.update(edtlib.load_vendor_prefixes_txt(prefixes_file))

    with tempfile.NamedTemporaryFile() as tfile:
        preprocess_header_files(args.dts, tfile.name)
        if args.preprocessed_dts_out:
            shutil.copy2(tfile.name, Path(args.preprocessed_dts_out).absolute())
        edt = edtlib.EDT(tfile.name, args.bindings_dirs,
                         # Suppress this warning if it's suppressed in dtc
                         warn_reg_unit_address_mismatch=True,
                         default_prop_types=True,
                         infer_binding_for_paths=["/zephyr,user"],
                         werror=args.edtlib_Werror,
                         vendor_prefixes=vendor_prefixes)

    if args.dts_out:
        # Save merged DTS source, as a debugging aid
        with open(Path(args.dts_out).absolute(), "w", encoding="utf-8") as f:
            print(edt.dts_source, file=f)

    # The raw index into edt.compat2nodes[compat] is used for node
    # instance numbering within a compatible.
    #
    # As a way to satisfy people's intuitions about instance numbers,
    # though, we sort this list so enabled instances come first.
    #
    # This might look like a hack, but it keeps drivers and
    # applications which don't use instance numbers carefully working
    # as expected, since e.g. instance number 0 is always the
    # singleton instance if there's just one enabled node of a
    # particular compatible.
    #
    # This doesn't violate any devicetree.h API guarantees about
    # instance ordering, since we make no promises that instance
    # numbers are stable across builds.
    for compat, nodes in edt.compat2nodes.items():
        edt.compat2nodes[compat] = sorted(
            nodes, key=lambda node: 0 if node.status == "okay" else 1)

    # populate all z_path_id first so any children references will
    # work correctly.
    for node in sorted(edt.nodes, key=lambda node: node.dep_ordinal):
        node.z_path_id = node_z_path_id(node)

    sorted_edt_nodes = sorted(edt.nodes, key=lambda node: (node.dep_ordinal, node.path, node.labels[0] if node.labels else ""))
    # for node in sorted_edt_nodes:
    #     print(node.path)

    busses = []
    for node in sorted_edt_nodes:
        if "spi@" in node.name and node.status == "okay":
            bus = {"name": node.labels[0], "devices": [], "externals": []}
            pw_pin = None
            cs_pins = [{"port": cs.controller.labels[0].replace("gpio", ""),
                        "pin": cs.data["pin"]}
                for cs in node.props["cs-gpios"].val]
            for dev in node.children.values():
                pdev = {"name": dev.name.split("@")[0], "cs": cs_pins[dev.unit_addr], "drdy": None}
                if (int_pins := dev.props.get("int-gpios")) is not None:
                    int_pin = int_pins.val[0]
                    pdev["drdy"] = {"port": int_pin.controller.labels[0].replace("gpio", ""), "pin": int_pin.data["pin"]}

                bus["devices"].append(pdev)
                pw_pin = dev.props.get("supply-gpios")
            if pw_pin is not None:
                pw_pin = pw_pin.val[0]
                bus["supply"] = {"port": pw_pin.controller.labels[0].replace("gpio", ""), "pin": pw_pin.data["pin"]}
            else:
                bus["supply"] = None
            busses.append(bus)

    spi_subs = {"versions": [{"name": sorted_edt_nodes[0].props["version"].val, "busses": busses}]}
    import json
    print(json.dumps(spi_subs, indent=4, sort_keys=True))

    env = jinja2.Environment(extensions=['jinja2.ext.do'],
                             undefined=jinja2.StrictUndefined)
    env.line_statement_prefix = '%%'
    env.line_comment_prefix = '%#'
    output = env.from_string(Path(args.template).read_text()).render(spi_subs)

    print(output)






def preprocess_header_files(dts_files, output_file, dependency_file=None):
    """
    DeviceTrees need to be preprocessed using the CPP.
    See the `zephyr/cmake/modules/dts.cmake` file for details.
    """

    system_include_dirs = ["include", "include/zephyr", "dts/common", "dts/arm", "dts",
                           "modules/hal/stm32/dts"]
    system_include_dirs = [zephyrpath(p) for p in system_include_dirs if zephyrpath(p).exists()]

    cpp_command = [
        "gcc",
        "-x assembler-with-cpp",
        "-nostdinc",
        # ${DTS_ROOT_SYSTEM_INCLUDE_DIRS}
        *[f"-isystem {p}" for p in system_include_dirs],
        # ${DTC_INCLUDE_FLAG_FOR_DTS}  # include the DTS source and overlays
        *[f"-include {f}" for f in dts_files],
        "-undef",
        "-D__DTS__",
        "-E",   # Stop after preprocessing
        # -MF ${DTS_DEPS} done seperately
        "-MD",  # Generate a dependency file as a side-effect
        # -o ${DTS_POST_CPP}
        "-o " + str(output_file),
        str(zephyrpath("misc/empty_file.c")),
    ]
    if dependency_file is not None:
        cpp_command.append("-MF " + str(dependency_file))

    command_str = " ".join(cpp_command)
    os.system(command_str)

    # print(command_str)


def setup_edtlib_logging():
    class LogFormatter(logging.Formatter):
        '''A log formatter that prints the level name in lower case,
        for compatibility with earlier versions of edtlib.'''

        def __init__(self):
            super().__init__(fmt='%(levelnamelower)s: %(message)s')

        def format(self, record):
            record.levelnamelower = record.levelname.lower()
            return super().format(record)

    # The edtlib module emits logs using the standard 'logging' module.
    # Configure it so that warnings and above are printed to stderr,
    # using the LogFormatter class defined above to format each message.

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(LogFormatter())

    logger = logging.getLogger('edtlib')
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)


def node_z_path_id(node):
    # Return the node specific bit of the node's path identifier:
    #
    # - the root node's path "/" has path identifier "N"
    # - "/foo" has "N_S_foo"
    # - "/foo/bar" has "N_S_foo_S_bar"
    # - "/foo/bar@123" has "N_S_foo_S_bar_123"
    #
    # This is used throughout this file to generate macros related to
    # the node.

    components = ["N"]
    if node.parent is not None:
        components.extend(f"S_{str2ident(component)}" for component in
                          node.path.split("/")[1:])

    return "_".join(components)


def str2ident(s):
    # Converts 's' to a form suitable for (part of) an identifier

    return re.sub('[-,.@/+]', '_', s.lower())

def parse_args():
    # Returns parsed command-line arguments

    parser = argparse.ArgumentParser()
    parser.add_argument("--dts", required=True, help="DTS file", action="append")
    parser.add_argument("--dtc-flags",
                        help="'dtc' devicetree compiler flags, some of which "
                             "might be respected here")
    parser.add_argument("--bindings-dirs", nargs='+', required=True,
                        help="directory with bindings in YAML format, "
                        "we allow multiple")
    # parser.add_argument("--header-out", required=True,
    #                     help="path to write header to")
    parser.add_argument("--dts-out",
                        help="path to write merged DTS source code to (e.g. "
                             "as a debugging aid)")
    parser.add_argument("--template")
    # parser.add_argument("--edt-pickle-out",
    #                     help="path to write pickled edtlib.EDT object to")
    parser.add_argument("--vendor-prefixes", action='append', default=[],
                        help="vendor-prefixes.txt path; used for validation; "
                             "may be given multiple times")
    parser.add_argument("--edtlib-Werror", action="store_true",
                        help="if set, edtlib-specific warnings become errors. "
                             "(this does not apply to warnings shared "
                             "with dtc.)")

    parser.add_argument("--preprocessed-dts-out",
                        help="path to write preprocessed DTS file to")

    return parser.parse_args()


if __name__ == "__main__":
    main()
