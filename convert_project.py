#!/usr/bin/env python3

"""Convert a project from Eclipse NLS approach to a ResourceBundle approach.

Requirements: Installed the Silver Surfer (ag).
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
logging.basicConfig(level=logging.WARNING,
                    format=' [%(levelname)-7s] (%(asctime)s) %(filename)s::%(lineno)d %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')


TEMPLATE_MESSAGE_VARIABLE = """{classname}.{variablename}"""
TEMPLATE_MESSAGE_TOSTRING = """{classname}.getString("{variablename}")"""
TEMPLATE_GETSTRING = """
  public static String getString(String key) {
    return MSG.getString(key);
  }"""


parser = argparse.ArgumentParser()
parser.add_argument("module_paths", nargs='+',
                    help="The path to the directory containing the module to convert")
parser.add_argument("--debug", action="store_true",
                    help="Set log level to debug")
parser.add_argument("--info", action="store_true",
                    help="Set log level to info")
parser.add_argument("--singleprocess", action="store_true",
                    help="Do not use multiprocessing")
parser.add_argument("--test", action="store_true",
                    help="Run tests")

def get_paths_to_all_NLS_classes(module_path):
    try:
        return str(sp.check_output(shlex.split("ag 'class.*extends.*NLS' -G '.*java$' {} -l".format(module_path))), encoding="utf-8").split()
    except sp.CalledProcessError:
        return []


def all_java_files_in(module_path, exclude_paths):
    """Get all Java files, except for the excluded ones (here used to exclude the NLS classes).
    >>> list(map(os.path.basename, all_java_files_in(os.path.dirname(os.path.abspath(__file__)), exclude_paths=[os.path.abspath("FAKE2.java")])))
    ['FAKE.java']
    """
    exclude = set(exclude_paths)
    try:
        paths = str(sp.check_output(shlex.split('find {} -iname "*.java"'.format(module_path))), encoding="utf-8").split()
    except sp.CalledProcessError:
        paths = []
    return [p for p in paths if not p in exclude]
    


def open_no_nl(filepath, mode="r"):
    """Open a file for reading without translating newlines."""
    return open(filepath, mode, newline="")

def NLS_variable_lines(filepath):
    lines = []
    with open_no_nl(filepath) as f:
        for i in f:
            if re.search("public.*static.*String", i) and not "(" in i:
                lines.append(i)
    return lines

def NLS_package(filepath):
    lines = []
    with open_no_nl(filepath) as f:
        for i in f:
            if i.startswith("package "):
                return i[len("package "):].replace(";", "").strip()
    raise ValueError("File %s has no package definition!", filepath)



def remove_lines_from_file(filepath, lines):
    """
    >>> lines = ["a\\n", "c\\n"]
    >>> filepath = "testfile-7ba084fc-e760-44f3-9a21-e5b52eaa5c25"
    >>> with open(filepath, "w") as f: f.write("a\\nb\\nc\\n")
    6
    >>> remove_lines_from_file(filepath, lines)
    True
    >>> with open(filepath) as f: f.read()
    'b\\n'
    >>> os.remove(filepath) # avoid leaking files
    """
    linesset = set(lines)
    towrite = []
    changed = False
    with open_no_nl(filepath) as f:
        for i in f:
            if i not in linesset:
                towrite.append(i)
            else:
                changed = True
    with open_no_nl(filepath, "w") as f:
        f.writelines(towrite)
    return changed

def filepath_to_classname(filepath):
    return os.path.splitext(os.path.basename(filepath))[0]

def line_to_variable(line):
    """
    >>> line_to_variable('   public static String CheckSelectorResultMessageFactory_SqlExceptionMessage;\\n\\r')
    'CheckSelectorResultMessageFactory_SqlExceptionMessage'
    >>> line_to_variable('  public static String ResultTableEditor_CHANGES_SUCCESFUL_STORED;\\n')
    'ResultTableEditor_CHANGES_SUCCESFUL_STORED'
    """
    # a variable cannot contain spaces, so we can split it to get the elements
    elements = line.split()
    for i, elem in enumerate(elements):
        if elem == "String":
            variable = elements[i+1] # the next one
            break
    else:
        raise ValueError("invalid line: did not contain String")
    # remove potential trailing semicolon
    return variable.replace(";", "")

def build_replacement_patterns(filesandlines, filesandpackages):
    """ Create a list of (FROM, TO, class, variable, package) tuples which provide the information for replacing.
    >>> fal = {"foo/Bah.Java": ["   public static String FOO_thing;\\n"]}
    >>> fap = {"foo/Bah.Java": "foo"}
    >>> build_replacement_patterns(fal, fap)
    [('Bah.FOO_thing', 'Bah.getString("FOO_thing")', 'Bah', 'FOO_thing', 'foo')]
    >>> fal = {"foo/Bah.Java": ["   public static String FOO_aing;\\n"], 
    ...        "foo/Baz.Java": ["   public static String FOO_ging;\\n", "   public static String FOO_ling;\\n"]}
    >>> fap = {"foo/Bah.Java": "foo", "foo/Baz.Java": "foo"}
    >>> build_replacement_patterns(fal, fap)
    [('Baz.FOO_ling', 'Baz.getString("FOO_ling")', 'Baz', 'FOO_ling', 'foo'), ('Baz.FOO_ging', 'Baz.getString("FOO_ging")', 'Baz', 'FOO_ging', 'foo'), ('Bah.FOO_aing', 'Bah.getString("FOO_aing")', 'Bah', 'FOO_aing', 'foo')]
    >>> fal = {"foo/0Bah.Java": ["   public static String FOO_aing;\\n"], 
    ...        "foo/Bah.Java": ["   public static String FOO_aing;\\n"]}
    >>> fap = {"foo/0Bah.Java": "foo", "foo/Bah.Java": "foo"}
    >>> build_replacement_patterns(fal, fap)
    [('0Bah.FOO_aing', '0Bah.getString("FOO_aing")', '0Bah', 'FOO_aing', 'foo'), ('Bah.FOO_aing', 'Bah.getString("FOO_aing")', 'Bah', 'FOO_aing', 'foo')]
    """
    FROM = TEMPLATE_MESSAGE_VARIABLE
    TO = TEMPLATE_MESSAGE_TOSTRING
    patterns = []
    
    for filepath, lines in sorted(filesandlines.items()):
        for line in lines:
            classname = filepath_to_classname(filepath)
            variablename = line_to_variable(line)
            fromstr = FROM.format(classname=classname, variablename=variablename)
            tostr = TO.format(classname=classname, variablename=variablename)
            package = filesandpackages[filepath]
            patterns.append((fromstr, tostr, classname, variablename, package))
    # reverse sort by variable and fromstr to ensure that longest
    # variables go first. This avoids partial replacements.
    return list(reversed(sorted(patterns, key=lambda x: (len(x[3]), x[3], len(x[2]), x[0]))))


@functools.lru_cache(maxsize=1000000)
def format_cached(formatstring, *args):
    """calls formatstring.(*args)"""
    return formatstring.format(*args)


@functools.lru_cache(maxsize=1000000)
def regexp_cached(matchstring):
    """use a larger cache for regexps. Note: this causes some double-caching, because regexp itself also caches."""
    return re.compile(matchstring)


def regex_replace_variable_safely(content, variablename, TO):
    """
    >>> rrvs = regex_replace_variable_safely
    >>> content = "messages.typeLabel = ObjectypeSelectionMessages_AttributeObjecttypeLabel;"
    >>> rrvs(content, "ObjectypeSelectionMessages_AttributeObjecttypeLabel", 'Messages.getString("ObjectypeSelectionMessages_AttributeObjecttypeLabel")')
    'messages.typeLabel = Messages.getString("ObjectypeSelectionMessages_AttributeObjecttypeLabel");'

    """
    # avoid replacing static-looking variables which are accessed via a class
    variablewithoutdot = regexp_cached('[^\\."]' + variablename + '[^"]') # avoid stumbling over already quoted ones
    variableatstart = regexp_cached('^[^"]' + variablename)
    logging.debug("variable %s, without. %s, atstart %s", variablename, variablewithoutdot, variableatstart)
    match = variablewithoutdot.search(content)
    while match is not None and match.start() < len(content):
        # manually replace the variable to avoid killing the first char in the match
        logging.debug("matched regexp %s as matcher %s", variablewithoutdot, match)
        content = content[:match.start() + 1] + TO + content[match.end() - 1:]
        match = variablewithoutdot.search(content, match.end() + len(TO) - len(variablename) - 1)
    match = variableatstart.search(content)
    while match is not None and match.start() < len(content):
        logging.debug("matched regexp %s as matcher %s", variableatstart, match)
        content = content[:match.start()] + TO + content[match.end():]
        match = variableatstart.search(content, match.end() + len(TO) - len(variablename))
    return content
    
    

def replace_NLS_usage(content, patterns):
    """
    >>> content = '''package net.disy.repository.designer.selector.view;
    ...
    ... import static net.disy.repository.designer.Messages.*;
    ... 
    ... public class ObjecttypeSelectionMessages {
    ... 
    ...   public static ObjecttypeSelectionMessages ForResultAttribute() {
    ...     ObjecttypeSelectionMessages messages = new ObjecttypeSelectionMessages();
    ...     messages.typeLabel = ObjectypeSelectionMessages_AttributeObjecttypeLabel;'''
    >>> FROM = 'Messages.ObjectypeSelectionMessages_AttributeObjecttypeLabel'
    >>> TO = 'Messages.getString("ObjectypeSelectionMessages_AttributeObjecttypeLabel")'
    >>> classname = 'Messages'
    >>> variablename = 'ObjectypeSelectionMessages_AttributeObjecttypeLabel'
    >>> package = 'net.disy.repository.designer'
    >>> patterns = [(FROM, TO, classname, variablename, package)]
    >>> print(replace_NLS_usage(content, patterns))
    package net.disy.repository.designer.selector.view;
    <BLANKLINE>
    import net.disy.repository.designer.Messages;
    <BLANKLINE>
    public class ObjecttypeSelectionMessages {
    <BLANKLINE>
      public static ObjecttypeSelectionMessages ForResultAttribute() {
        ObjecttypeSelectionMessages messages = new ObjecttypeSelectionMessages();
        messages.typeLabel = Messages.getString("ObjectypeSelectionMessages_AttributeObjecttypeLabel");

    >>> content = '''package net.disy.gis.lais.dialog;
    ... 
    ... import static net.disy.gis.lais.dialog.LaisMessages.*;
    ... 
    ...       if (massnahmeWithError == null) {
    ...         massnahmeWithError = getErrorMassnahme(massnahmeWithUnusedArea);
    ...       }
    ...       errorMessage.append(LaisErrorAggregator_AreaInMassnamenUnused);
    ...     }'''
    >>> FROM = 'LaisMessages.LaisErrorAggregator_AreaInMassnamenUnused'
    >>> TO = 'LaisMessages.getString("LaisErrorAggregator_AreaInMassnamenUnused")'
    >>> classname = 'LaisMessages'
    >>> variablename = 'LaisErrorAggregator_AreaInMassnamenUnused'
    >>> package = 'net.disy.gis.lais.dialog'
    >>> patterns = [(FROM, TO, classname, variablename, package)]
    >>> patterns == [('LaisMessages.LaisErrorAggregator_AreaInMassnamenUnused', 'LaisMessages.getString("LaisErrorAggregator_AreaInMassnamenUnused")', 'LaisMessages', 'LaisErrorAggregator_AreaInMassnamenUnused', 'net.disy.gis.lais.dialog')]
    True
    >>> print(replace_NLS_usage(content, patterns))
    package net.disy.gis.lais.dialog;
    <BLANKLINE>
    import net.disy.gis.lais.dialog.LaisMessages;
    <BLANKLINE>
          if (massnahmeWithError == null) {
            massnahmeWithError = getErrorMassnahme(massnahmeWithUnusedArea);
          }
          errorMessage.append(LaisMessages.getString("LaisErrorAggregator_AreaInMassnamenUnused"));
        }
    """
    replaced = set() # never replace the same twice
    for FROM, TO, classname, variablename, package in patterns:
        classimport = format_cached("import {}.{}", package, classname)
        staticimport = format_cached("import static {}.{}", package, classname)
        staticimportvariable = format_cached("import static {}.{}", package, FROM)
        staticimportstar = format_cached("import static {}.{}.*", package, classname)
        logging.debug("FROM %s, TO %s, classname %s, variablename %s, package %s",
                      FROM, TO, classname, variablename, package)
        logging.debug("staticimport %s", staticimport)
        logging.debug("staticimportvariable %s", staticimportvariable)
        logging.debug("staticimportstar %s", staticimportstar)
        uses_static_variable_import_matcher = regexp_cached(staticimportvariable.replace(".", "\\."))
        uses_static_star_import_matcher = regexp_cached(staticimportstar.replace(".", "\\.").replace("*", "\\*"))
        if staticimportvariable not in replaced:
            if uses_static_variable_import_matcher.search(content) is not None:
                replaced.add(staticimportvariable)
                logging.debug("replace staticimport %s by %s", staticimportvariable, classimport)
                content = content.replace(staticimportvariable, classimport)
                if variablename not in replaced:
                    replaced.add(variablename)
                    logging.debug("replace staticimportvariable %s by %s", variablename, TO)
                    content = regex_replace_variable_safely(content, variablename, TO)
        if uses_static_star_import_matcher.search(content) is not None:
            if variablename not in replaced:
                replaced.add(variablename)
                logging.debug("replace staticimportstarvariable %s by %s", variablename, TO)
                content = regex_replace_variable_safely(content, variablename, TO)
        if FROM not in replaced:
            if FROM in content:
                replaced.add(FROM)
                logging.debug("replace FROM %s by %s", FROM, TO)
                content = content.replace(FROM, TO)
    # must replace the importstar import in the end, because I use it
    # to detect whether we have a star import
    for FROM, TO, classname, variablename, package in patterns:
        classimport = format_cached("import {}.{}", package, classname)
        staticimportstar = format_cached("import static {}.{}.*", package, classname)
        uses_static_star_import_matcher = regexp_cached(staticimportstar.replace(".", "\\.").replace("*", "\\*"))
        if uses_static_star_import_matcher.search(content) is not None:
            if staticimportstar not in replaced:
                replaced.add(staticimportstar)
                logging.debug("replace staticimportstar %s by %s", staticimportstar, classimport)
                content = content.replace(staticimportstar, classimport)
    return content



def replace_patterns_in_file(filepath, patterns):
    """Replace the first element of every tuple in patterns with the second."""
    with open_no_nl(filepath) as f:
        content = f.read()
    logging.info("replacing Message variable access with ResourceBundle calls in %s", filepath)
    newcontent = replace_NLS_usage(content, patterns)
    changed = newcontent != content
    if changed:
        with open_no_nl(filepath, "w") as f:
            f.write(newcontent)
    return changed


def add_import_to_string(content, existing_import):
    """
    >>> content = '''// copyright
    ... package some.package;
    ... 
    ... import some.other;
    ... 
    ... import some.other;'''
    >>> print(add_import_to_string(content, existing_import="import some.other"))
    // copyright
    package some.package;
    <BLANKLINE>
    import net.disy.commons.core.locale.IMessageResolver;
    import net.disy.commons.core.locale.ResourceBundleMessageResolver;
    import java.util.MissingResourceException;
    <BLANKLINE>
    import some.other;
    <BLANKLINE>
    import some.other;
    """
    try:
        import_idx = content.index(existing_import)
    except ValueError: # not found
        return logging.error("File %s has no NLS import!", filepath)

    additional_imports = [
        "import net.disy.commons.core.locale.IMessageResolver",
        "import net.disy.commons.core.locale.ResourceBundleMessageResolver",
        "import java.util.MissingResourceException"
    ]

    importstring = ";\n".join(additional_imports) + ";\n\n"
    # before import org.eclipse.osgi.util.NLS;
    # the files are all the same, I can simply add the string
    content = (content[:import_idx]
               + importstring
               + content[import_idx:])
    return content


def add_import_before(filepath):
    with open_no_nl(filepath) as f:
        content = f.read()
    
    content = add_import_to_string(
        content,
        existing_import="import org.eclipse.osgi.util.NLS")

    with open_no_nl(filepath, "w") as f:
        f.write(content)

def replace_static_constructor_with_resolver(content):
    """
    >>> content = "\\nstatic {\\n moooooo\\n\\n     moo}\\n"
    >>> replace_static_constructor_with_resolver(content)
    '\\nprivate static final IMessageResolver MSG = new ResourceBundleMessageResolver(BUNDLE_NAME);\\n'

    """
    ResourceBundle = "private static final IMessageResolver MSG = new ResourceBundleMessageResolver(BUNDLE_NAME);"
    static = "static {"
    startidx = content.index(static) # I checked that this finds ALL instances, indentation is kept as is.
    stopidx = content[startidx+len(static):].index("}")
    return (content[:startidx]
            + ResourceBundle
            + content[startidx + len(static) + stopidx + 1:])

def add_to_end_of_last_class(content, block=TEMPLATE_GETSTRING):
    """Add a getString method at the end of the file
    >>> content = "moo {\\n  {\\n\\n  }\\n}"
    >>> add_to_end_of_last_class(content, block="  abc")
    'moo {\\n  {\\n\\n  }  abc\\n}'
    """
    endidx = content.rindex("\n}")
    
    return (content[:endidx]
            + block
            + content[endidx:])
    

def cleanup_empty_lines(content):
    """Allow at most two consecutive empty lines
    >>> content = 'abc\\n\\n\\n\\nbc\\n\\nc\\n\\n\\n\\nd\\n\\n'
    >>> cleanup_empty_lines(content)
    'abc\\n\\n\\nbc\\n\\nc\\n\\n\\nd\\n'
    >>> content = '''-  static {
    ...     // initialize resource bundle
    ...     NLS.initializeMessages(BUNDLE_NAME, Messages.class);
    ...   }
    ... }'''
    >>> content == cleanup_empty_lines(content)
    True
    >>> content + "\\n" == cleanup_empty_lines(content + "\\n")
    True
    """
    lines = content.split("\n")
    nlines = len(lines)
    newlines = []
    prev, prevprev = None, None
    def all_empty(*args):
        empty = [(l is not None
                  and l.strip() == "")
                 for l in args]
        return not False in empty
    for idx, line in enumerate(lines):
        if (not all_empty(prevprev, prev, line)
            and (idx != nlines - 1 or not all_empty(prev, line))):
            newlines.append(line)
        prevprev = prev
        prev = line
    return "\n".join(newlines)


def rewrite_NLS_Messages_file(filepath, variablelines):
    """Turn a NLS Messages class into a ResourceBundle class.

- Remove the variable_lines from the filepath,
- Add import MissingResourceException and ResourceBunde;
- replace the static constructor with a private static final ResourceBundle initialization, and 
- add a getString(key) method.
- remove consecutive empty lines
- remove " extends NLS"
"""
    logging.info("rewriting Message file %s", filepath)
    # first remove all the variable lines with efficient iteration over the lines
    changed = remove_lines_from_file(filepath, variablelines)
    # process the content of the NLS Message file in memory
    with open_no_nl(filepath) as f:
        content = f.read()
    try:
        rewritten = replace_static_constructor_with_resolver(content)
    except ValueError:
        logging.warning("No static block found in file %s", filepath)
        return False # not changed
    rewritten = add_import_to_string(
        rewritten,
        existing_import="import org.eclipse.osgi.util.NLS")
    rewritten = add_to_end_of_last_class(rewritten, TEMPLATE_GETSTRING)
    rewritten = cleanup_empty_lines(rewritten)
    rewritten = rewritten.replace(" extends NLS", "")
    changed = changed or rewritten != content
    if changed:
        with open_no_nl(filepath, "w") as f:
            f.write(rewritten)
    return changed


def replace_patterns_in_filelist(filepaths, patterns):
    return [replace_patterns_in_file(i, patterns)
            for i in filepaths]

def process_multiprocessing(sublists, patterns, usecpus):
    with concurrent.futures.ProcessPoolExecutor(max_workers=usecpus) as e:
        futures = []
        for sub in sublists:
            futures.append(e.submit(replace_patterns_in_filelist, sub, patterns))
    changedusage = []
    for fut in futures:
        changedusage.extend(fut.result(timeout=900))
    return changedusage

def process_single_process(sublists, patterns):
    changedusage = []
    for sub in sublists:
        changedusage.extend(replace_patterns_in_filelist(sub, patterns))
    return changedusage


def main(args):
    # enforce absolute path (interpreted the same by different tools)
    module_paths = [os.path.abspath(i) for i in args.module_paths]
    NLS_files = []
    for module_path in module_paths:
        NLS_files.extend(get_paths_to_all_NLS_classes(module_path))
    filesandlines = {}
    filesandpackages = {}
    for i in NLS_files:
        filesandlines[i] = NLS_variable_lines(i)
    for i in NLS_files:
        filesandpackages[i] = NLS_package(i)
    patterns = build_replacement_patterns(filesandlines, filesandpackages)
    # logging.debug("All patterns: %s", patterns)
    alljavafiles = []
    for module_path in module_paths:
        alljavafiles.extend(all_java_files_in(module_path, exclude_paths=NLS_files))
    usecpus = 2 * multiprocessing.cpu_count()
    listcount = 3 * usecpus
    # process the files in order: distribute them in order between the processes
    sublists = [[] for i in range(listcount)]
    for n, i in enumerate(alljavafiles):
        sublists[n % listcount].append(i)
    if args.singleprocess:
        changedusage = process_single_process(sublists, patterns)
    else:
        changedusage = process_multiprocessing(sublists, patterns, usecpus)
    changedmessage = [rewrite_NLS_Messages_file(i, filesandlines[i])
                      for i in NLS_files]
    return changedusage, changedmessage



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
