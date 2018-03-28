#!/usr/bin/env python3

"""Prepare setup files for JInto completion.

Creates the files PROJECT/.settings/de.guhsoft.jinto.core.prefs and a tarball with those files which can be unpacked to get the files to systems where this script cannot run.

Example usage: ./eclipse_jinto_setup.py -p ~/eclipse-workspace/cadenza-trunk/cadenza -t ~/eclipse-workspace/cadenza-trunk/jinto-init.tar.gz ~/eclipse-workspace/cadenza-trunk/cadenza/*/

Requirements: The Silver Surfer (ag).
"""

import argparse
import subprocess as sp
import os
import shlex
import re
import logging
import functools
import multiprocessing
import concurrent.futures
logging.basicConfig(level=logging.INFO,
                    format=' [%(levelname)-7s] (%(asctime)s) %(filename)s::%(lineno)d %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

BUNDLE_DECLARATION = "private static final String BUNDLE_NAME"
CLASS_DECLARATION = "public class "

TEMPLATE = """de.guhsoft.jinto.core.accessorConfiguration=<?xml version\\="1.0" encoding\\="UTF-8"?>\\n<root>\\n{resource_bundle_reference}\\n</root>
eclipse.preferences.version=1
"""

TEMPLATE_RESOURCE_BUNDLE_REFERENCE_TAG = """<resourceBundleReference resourceBundleName\\="{properties}">\\n<accessor typeName\\="{accessor}">\\n<methodReference methodName\\="getString">\\n<parameter index\\="0" isSelected\\="true" parameterName\\="key" parameterType\\="java.lang.String"/>\\n</methodReference>\\n</accessor>\\n</resourceBundleReference>"""

parser = argparse.ArgumentParser()
parser.add_argument("module_paths", nargs='+',
                    help="The paths to the directories of the projects to process (a project is a folder which can have a .settings folder that gets recognized in Eclipse)")
parser.add_argument("--debug", action="store_true",
                    help="Set log level to debug")
parser.add_argument("--info", action="store_true",
                    help="Set log level to info")
parser.add_argument("-p", "--parent-directory",
                    help="The path to the parent directory of the modules, files will be stored relative to this")
parser.add_argument("-t", "--target-tarball-path",
                    help="The path to the tarball to create")
parser.add_argument("--test", action="store_true",
                    help="Run tests")


def get_paths_to_all_Messages_classes(module_path):
    try:
        return str(sp.check_output(shlex.split("ag 'static.*final.*IMessageResolver.*MSG.*=' -G '.*java$' {} -l".format(module_path))), encoding="utf-8").split()
    except sp.CalledProcessError:
        return []


def process_bundle_template(accessor, properties):
    """fill the properties and accessor in the template

    :param properties: package+file of the properties file, i.e. net.disy.cadenza.desktop.messages
    :param accessor: package+class of the accessing class (the one with .getString()), i.e. net.disy.cadenza.desktop.Messages

    >>> process_bundle_template("net.disy.cadenza.desktop.Messages", "net.disy.cadenza.desktop.messages")
    '<resourceBundleReference resourceBundleName\\\\="net.disy.cadenza.desktop.messages">\\\\n<accessor typeName\\\\="net.disy.cadenza.desktop.Messages">\\\\n<methodReference methodName\\\\="getString">\\\\n<parameter index\\\\="0" isSelected\\\\="true" parameterName\\\\="key" parameterType\\\\="java.lang.String"/>\\\\n</methodReference>\\\\n</accessor>\\\\n</resourceBundleReference>'
"""
    return TEMPLATE_RESOURCE_BUNDLE_REFERENCE_TAG.format(accessor=accessor,
                                                         properties=properties)

def generate_settings_data(accessors_and_properties):
    """
    :param accessors_and_properties: [(accessor, properties), ...]
    >>> generate_settings_data([('net.disy.cadenza.desktop.Messages', 'net.disy.cadenza.desktop.messages')])
    'de.guhsoft.jinto.core.accessorConfiguration=<?xml version\\\\="1.0" encoding\\\\="UTF-8"?>\\\\n<root>\\\\n<resourceBundleReference resourceBundleName\\\\="net.disy.cadenza.desktop.messages">\\\\n<accessor typeName\\\\="net.disy.cadenza.desktop.Messages">\\\\n<methodReference methodName\\\\="getString">\\\\n<parameter index\\\\="0" isSelected\\\\="true" parameterName\\\\="key" parameterType\\\\="java.lang.String"/>\\\\n</methodReference>\\\\n</accessor>\\\\n</resourceBundleReference>\\\\n</root>\\neclipse.preferences.version=1\\n'
    >>> generate_settings_data([('Ac', 'Ap'), ('Bc', 'Bp')])
    'de.guhsoft.jinto.core.accessorConfiguration=<?xml version\\\\="1.0" encoding\\\\="UTF-8"?>\\\\n<root>\\\\n<resourceBundleReference resourceBundleName\\\\="Ap">\\\\n<accessor typeName\\\\="Ac">\\\\n<methodReference methodName\\\\="getString">\\\\n<parameter index\\\\="0" isSelected\\\\="true" parameterName\\\\="key" parameterType\\\\="java.lang.String"/>\\\\n</methodReference>\\\\n</accessor>\\\\n</resourceBundleReference>\\\\n<resourceBundleReference resourceBundleName\\\\="Bp">\\\\n<accessor typeName\\\\="Bc">\\\\n<methodReference methodName\\\\="getString">\\\\n<parameter index\\\\="0" isSelected\\\\="true" parameterName\\\\="key" parameterType\\\\="java.lang.String"/>\\\\n</methodReference>\\\\n</accessor>\\\\n</resourceBundleReference>\\\\n</root>\\neclipse.preferences.version=1\\n'
    """
    return TEMPLATE.format(
        resource_bundle_reference="\\n".join(
            process_bundle_template(accessor, properties)
            for accessor, properties in accessors_and_properties))

def all_accessors_and_properties(module_path):
    """Finds all classes and assosiated properties files under the path.

    :returns: [(accessor, properties), ...]
    """
    return get_paths_to_all_Messages_classes(module_path)

    
def extract_accessor_and_properties(filepath):
    """:returns: (accessor, properties)"""
    package = None
    properties = None
    classname = None
    with open(filepath) as f:
        for line in f:
            if package is None and line.strip().startswith("package"):
                package = line.replace(";", "").replace("package", "").strip()
            if properties is None and BUNDLE_DECLARATION in line:
                properties = line.replace(
                    BUNDLE_DECLARATION, "").replace(
                        '"', '').replace(
                            '=', '').replace(
                                ";", "").strip()
                # remove possibly included comments
                if "/" in properties:
                    properties = properties[:properties.index("/")].strip()
            if classname is None and CLASS_DECLARATION in line:
                classname = line.replace(
                    CLASS_DECLARATION, "").replace(
                        '{', '').strip()
            if package is not None and properties is not None and classname is not None:
                break
    if package is None:
        raise ValueError("File %s misses package identifier: %s.", filepath)
    if properties is None:
        raise ValueError("File %s misses properties identifier: %s.", filepath)
    if classname is None:
        raise ValueError("File %s misses class identifier: %s.", filepath)
    return package+"."+classname, properties


def write_jinto_settings_file(module_path, data):
    """:returns: the path to the settings file."""
    settings_dirpath = os.path.join(module_path, ".settings")
    settings_filepath = os.path.join(settings_dirpath, "de.guhsoft.jinto.core.prefs")
    try:
        os.makedirs(settings_dirpath)
    except FileExistsError:
        pass # no need to do anything
    if os.path.exists(settings_filepath):
        logging.error("Not writing settings file %s : file already exists. What would have been written: %s", settings_filepath, data)
    else:
        with open(settings_filepath, "w") as f:
            f.write(data)
        return settings_filepath

def create_tarball(tarball_path, file_paths, base_path):
    cmd = ('cd "{}" && tar -czf "{}" '.format(base_path, tarball_path)
           + ' '.join('"' + p + '"'
                      for p in file_paths))
    logging.debug(cmd)
    try:
        return str(sp.check_output(cmd, shell=True), encoding="utf-8").split()
    except sp.CalledProcessError:
        return []


def main(args):
    # enforce absolute path (interpreted the same by different tools)
    module_paths = [os.path.abspath(i) for i in args.module_paths]
    file_paths = []
    for module_path in module_paths:
        if not os.path.isdir(module_path):
            logging.warn("Not a directory %s", module_path)
            continue
        filepaths = get_paths_to_all_Messages_classes(module_path)
        accessors_and_properties = [extract_accessor_and_properties(i) for i in filepaths]
        if accessors_and_properties:
            data = generate_settings_data(accessors_and_properties)
            filepath = write_jinto_settings_file(module_path, data)
            if filepath is not None:
                relpath = os.path.relpath(filepath, args.parent_directory)
                file_paths.append(relpath)
    logging.info("Files created:")
    print("\n".join(os.path.join(args.parent_directory, i)
                    for i in file_paths
                    if i is not None))
    create_tarball(args.target_tarball_path, file_paths, args.parent_directory)


# output test results as base60 number (for aesthetics)
def numtosxg(n):
    CHARACTERS = ('0123456789'
                  'ABCDEFGHJKLMNPQRSTUVWXYZ'
                  '_'
                  'abcdefghijkmnopqrstuvwxyz')
    s = ''
    if not isinstance(n, int) or n == 0:
        return '0'
    while n > 0:
        n, i = divmod(n, 60)
        s = CHARACTERS[i] + s
    return s


def _test(args):
    """  run doctests, can include setup. """
    from doctest import testmod
    tests = testmod()
    if not tests.failed:
        return "^_^ ({})".format(numtosxg(tests.attempted))
    else: return ":( "*tests.failed
    
if __name__ == "__main__":
    args = parser.parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    if args.info:
        logging.getLogger().setLevel(logging.INFO)
    if args.test:
        print(_test(args))
    else:
        main(args)
