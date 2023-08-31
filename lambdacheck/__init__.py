from cmath import exp
import emoji
import nbformat
import logging
import nbconvert.preprocessors as preprocessors
from typing import Optional, List
from pydantic import BaseModel
import re
from enum import Enum
from difflib import SequenceMatcher
from rich.console import Console, Group, NewLine
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.table import Table
import json
import os

logging.basicConfig()


def open_notebook(path):
    "Opens a notebook from the path"
    with open(path, 'r') as f:
        nb = nbformat.read(f, as_version=4)
    logging.debug("[%s] with %d cells." % (path, len(nb.cells)))
    return nb


def write_notebook(nb, path):
    "write the notebook to the given path"
    with open(path, 'w') as f:
        nbformat.write(nb, f)


def execute_nb(nb, timeout=60, working_dir=None, kernel=None):
    """
    Executes the notebook
    """

    if not kernel:
        kernel = nb.metadata.kernelspec.name
    proc = preprocessors.ExecutePreprocessor(
        timeout=timeout,
        kernel_name=kernel,
        allow_errors=True)
    if working_dir:
        ctx = {"metadata": {"path": working_dir}}
    else:
        ctx = {}
    proc.preprocess(nb, ctx)


def copy(nb):
    return nbformat.from_dict(nb)


def clear_output(nb):
    for cell in nb.cells:
        if cell.cell_type == 'code':
            cell.outputs = []

class Directives(BaseModel):
    solution: Optional[bool]
    masterOnly: Optional[bool]
    workUnit: Optional[bool]    # student's work go here
    check: Optional[bool]       # unit test for formative assessments
    test: Optional[bool]        # unit test for summative assessments
    title: Optional[str]
    grade: float = 1.
    match: Optional[str]        # TODO: Literal[word, line]
    normalize: Optional[str]    # 

    class Config:
        extra = 'forbid'


#
# Comment character of #, // or ;
# followed by spaces
# followed by @(.*)
# the captured is the directive definition.
#
directive_pattern = re.compile(r'^(?:#|//|;)\s+@(.*)')


def get_directives(cell) -> Directives:
    "extract the directives from cell source"
    def entry(line):
        "get a single entry from the line"
        m = directive_pattern.match(line)
        if m:
            match = m.group(1).strip()
            if ':' in match:
                (key, value) = match.split(":", 1)
                value = value.strip()
            else:
                key, value = match, True
            return key.strip(), value
        return None

    # get the leading comment block
    lines = [line for line in cell.source.split("\n") if line.strip()]
    return Directives(**dict(x for x in map(entry, lines) if x))


#
# normalization
#
class NormalizeOptions(BaseModel):
    lower: bool = False
    whitespace: bool = False
    strip: bool = False
    maskAddresses: bool = True
    maskAnsi: bool = True
    ignoreblanks: bool = True
    round: Optional[int]
    class Config:
        extra = 'forbid'

def normalize_string(data: str, options: NormalizeOptions):
    data = data.rstrip()
    if options.strip:
        data = data.strip()
    if options.lower:
        data = data.lower()
    if options.whitespace:
        data = re.sub(r'\s+', ' ', data)
    if options.ignoreblanks:
        data = re.sub(r'^\s*$', '', data)
    if options.maskAddresses:
        data = re.sub(r'at 0x[a-f0-9]+', 'at 0x***', data)
        data = re.sub(r'<ipython-input-[^>]*>', '<ipython-input-***>', data)
    if options.maskAnsi:
        data = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', data)
    if options.round:
        digits = int(options.round)
        def replacer(m):
            a, b = m.group(1), m.group(2)
            if len(b) > digits:
                b = b[:digits] + (" " * (len(b) - digits))
            return a + "." + b

        data = re.sub(r'\b(\d)+\.(\d+)\b', replacer, data)
    return data


def normalize(data, options: NormalizeOptions):
    digits = options.round

    if isinstance(data, str):
        data = normalize_string(data, options)
    elif isinstance(data, float):
        if digits:
            data = round(data, digits)
    elif isinstance(data, list):
        for i in range(len(data)):
            data[i] = normalize(data[i], options)
    elif isinstance(data, dict):
        for k in data.keys():
            data[k] = normalize(data[k], options)
    return data


class CellResultType(str, Enum):
    "Cell result.  A cell output can have multiple cell results"
    execute_result = "execute_result"
    error = "error"
    stdout = "stdout"
    stderr = "stderr"
    empty = "empty"
    unknown = 'unknown'


class CellResult(BaseModel):
    id: str
    directives: Directives
    text: str = ""
    type: CellResultType

def get_cell_stream_result(directives: Directives, cell) -> Optional[CellResult]:
    "Gets the CellResult from the cell stream output."
    result = None
    if 'outputs' in cell:
        for output in cell.outputs:
            if output.output_type == 'stream':
                text = output.text or ""
                result = CellResult(id=cell.id, 
                    type=CellResultType[output.name],
                    text=text.rstrip(),
                    directives=directives)
                break
    return result

def get_cell_execute_result(directives: Directives, cell) -> Optional[CellResult]:
    "Get the execute result.  text/plain is the default result."
    result = None
    if 'outputs' in cell:
        for output in cell.outputs:
            if output.output_type == 'execute_result':
                result = CellResult(id=cell.id, type=CellResultType.execute_result, directives=directives)
                data = output.data
                if 'text/plain' in data:
                    result.text = data['text/plain']
                else:
                    result.text = str(data)
                result.text = result.text.rstrip()
                break
    return result

def get_cell_error_result(directives, cell) -> Optional[CellResult]:
    "Get the error result"
    result = None
    if 'outputs' in cell:
        for output in cell.outputs:
            if output.output_type == 'error':
                result = CellResult(id=cell.id, type=CellResultType.error, directives=directives)
                result.text = "\n".join([
                    "ENAME: %s" % output.ename,
                    "EVALUE: %s" % output.evalue,
                ])
                break
    return result

def get_cell_result(directives: Directives, cell) -> CellResult:
    "Get CellResult from cell.  Try error, then stream, then execute result."
    if 'outputs' in cell:
        result = get_cell_error_result(directives, cell)
        if result: return result

        result = get_cell_stream_result(directives, cell)
        if result: return result

        result = get_cell_execute_result(directives, cell)
        if result: return result

        # otherwise
        #for output in cell.outputs:
        #    result = CellResult(id=cell.id, type=CellResultType.unknown, text=str(output))
        #    return result

    return CellResult(id=cell.id, type=CellResultType.empty, directives=directives)


#
# cell transformations
#

def convert_work_unit_to_code(cell):
    new_cell = nbformat.v4.new_code_cell()
    new_cell.source = cell.source
    new_cell.metadata.update(cell.metadata)
    return new_cell


def apply_directives(directives: Directives, lead: str, cell):
    "apply the directives to the cell metadata"

    if directives.workUnit:
        cell.source = '{} {}\n'.format(lead, emoji.emojize(":writing_hand:")) + cell.source
    else:
        # make it readOnly
        cell.metadata['editable'] = False
        cell.source = '{} {}\n'.format(lead, emoji.emojize(":locked:")) + cell.source


def clean_outputs(cell):
    "Clears the outputs of a single cell"
    if 'outputs' in cell:
        cell.outputs = []
    if 'execution_count' in cell:
        cell.execution_count = None


def get_comment_lead_chars(nb):
    lead = '#'
    try:
        kernel = nb.metadata['kernelspec']['name']
        kernel = kernel.lower()
        if 'java' in kernel or 'kotlin' in kernel or 'scala' in kernel:
            lead = '//'
        elif 'clojure' in kernel:
            lead = ';'
    except KeyError:
        pass
    return lead


def make_worksheet(master):
    worksheet = copy(master)
    worksheet.cells = []
    lead = get_comment_lead_chars(master)

    for cell in master.cells:
        if cell.cell_type == 'code' or cell.cell_type == 'raw':
            directives = get_directives(cell)

            # skip solution cells
            if directives.solution:
                continue

            # skip masterOnly cells
            if directives.masterOnly:
                continue

            # skip empty cells
            if cell.source.strip() == '':
                continue

            new_cell = copy(cell)
            if directives.workUnit:
                new_cell = convert_work_unit_to_code(new_cell)

            clean_outputs(new_cell)
            apply_directives(directives, lead, new_cell)
        elif cell.cell_type == 'markdown':
            new_cell = copy(cell)
            new_cell.metadata['editable'] = False
        else:
            new_cell = cell
        worksheet.cells.append(new_cell)
    return worksheet


class ResultWithDirectives(BaseModel):
    "Expected result"
    id: str
    directives: Directives
    result: CellResult

def write_expected(expected_list: List[CellResult], filename: str):
    "write a list of expected results to a JSON file"

    with open(filename, 'w') as f:
        data = [x.dict() for x in expected_list]
        json.dump(data, f, indent=4)


def open_expected(filename: str) -> List[CellResult]:
    "read a list of expected from a JSON file"
    with open(filename, 'r') as f:
        data = json.load(f)
        return [CellResult(**x) for x in data]
    
def get_normalize_options(directives: Directives) -> NormalizeOptions:
    options = NormalizeOptions()
    # TODO: build options using directives
    return options

def make_expected_list(master, test=False, options=None):
    expected_list: List[CellResult] = []

    for cell in master.cells:
        directives = get_directives(cell)
        if directives.check or (test and directives.test):
            result = get_cell_result(directives, cell)
            result.text = normalize(result.text, get_normalize_options(directives))
            expected_list.append(result)
    return expected_list

def print_expected(expected_list: List[CellResult], console=None):
    if console is None:
        console = Console()

    for expected in expected_list:
        directives = expected.directives
        title = directives.title or expected.id
        subtitle = expected.type
        text = expected.text
        panel = Panel(text, title=title, subtitle=subtitle, padding=1)
        console.print(panel)
        console.print("\n")


class TestResultStatus(str, Enum):
    success = 'success'
    failure = 'failure'
    error = 'error'


class TestResult(BaseModel):
    id: str
    submitted: Optional[CellResult]
    expected: CellResult
    status: TestResultStatus
    message: str = ""
    ratio: float
    grade: float
    total: float

    class Config:
        extra = 'forbid'


class TestReport(BaseModel):
    title_prefix: Optional[str]
    results: List[TestResult]
    grade: float = 0.
    total: float = 0.


def get_tokens(directives: Directives, text: str) -> List[str]:
    "break text according to the directives"
    match = directives.match
    if match is None:
        return [text]
    elif 'line' in match:
        return text.split("\n")
    elif 'word' in match:
        return text.split()
    else:
        return [text]


def get_test_result(submitted: Optional[CellResult], expected: CellResult) -> TestResult:
    directives = expected.directives
    title = directives.title or ""
    total = directives.grade

    if submitted is None:
        return TestResult(
            id=expected.id,
            expected=expected,
            status=TestResultStatus.error,
            message="[\"%s\"] is not submitted." % title,
            ratio=0.,
            grade=0.0,
            total=total,
        )
    else:
        if not submitted.type == expected.type:
            return TestResult(
                id=expected.id,
                submitted=submitted,
                expected=expected,
                status=TestResultStatus.failure,
                ratio=0.0,
                grade=0.0,
                total=total,
            )
        else:
            options = get_normalize_options(directives)
            a = get_tokens(directives, normalize(submitted.text, options))
            b = get_tokens(directives, normalize(expected.text, options))
            ratio = SequenceMatcher(None, a, b).ratio()
            if ratio > 0.99:
                status = TestResultStatus.success
            else:
                status = TestResultStatus.failure

            return TestResult(
                id=expected.id,
                submitted=submitted,
                expected=expected,
                status=status,
                ratio=ratio,
                grade=total * ratio,
                total=total,
            )

def make_report(nb, expected_list: List[CellResult], title_prefix: Optional[str] = None) -> TestReport:
    results = []
    grade = 0.
    total = 0.

    # index cells by ID
    cells = dict(
        (cell.id, cell) for cell in nb.cells
    )

    # index cells by title
    for cell in nb.cells:
        cell_directives = get_directives(cell)
        if cell_directives.title:
            cells[cell_directives.title] = cell

    for expected in expected_list:
        cell_id = expected.id
        directives = expected.directives
        title = directives.title or cell_id

        if (title_prefix is not None) and (not title.startswith(title_prefix)):
            continue

        # get the submitted cell
        if cell_id in cells:
            submitted_cell = cells[cell_id]
        elif title in cells:
            submitted_cell = cells[title]
        else:
            submitted_cell = None

        submitted_result = get_cell_result(directives, submitted_cell) if submitted_cell else None

        result = get_test_result(submitted_result, expected)

        results.append(result)
        grade += result.grade
        total += result.total

    return TestReport(
        title_prefix=title_prefix,
        results=results,
        grade=grade,
        total=total,
    )


def output_test_result(i: int, result: TestResult) -> Group:
    directives = result.expected.directives
    match = directives.match or 'exact'
    title = directives.title or result.id
    percent = result.ratio * 100

    group = [
        Text("%d. %s\n" % (i+1, title), style='bold underline'),
    ]

    if result.status == TestResultStatus.error:
        expected = Panel(result.expected.text, title="Expected %s" % result.expected.type.value, style='grey30')
        group.extend([
            Text("Error: %s" % result.message, style='red'),
            NewLine(),
            expected,
        ])

    elif result.status == TestResultStatus.failure and result.submitted:
        submitted = Panel(result.submitted.text, title="Submitted %s" % result.submitted.type.value, style='black')
        expected = Panel(result.expected.text, title="Expected %s" % result.expected.type.value, style='black')
        group.extend([
            Text("Failure: %.2f%% using %s matching" % (percent, match), style='red'),
            NewLine(),
            Columns([submitted, expected]),
        ])

    elif result.status == TestResultStatus.success:
        group.extend([
            Text("Success.", style='green bold'),
            Text(emoji.emojize("Well done. :thumbs_up:"), style='green'),
        ])

    group.append(
        Text("%.1f / %.1f" % (result.grade, result.total), justify='right', style='bold')
    )

    return Group(*group)


def print_report(report: TestReport, console=None):
    if console == None:
        console = Console()
    for (i, result) in enumerate(report.results):
        panel = Panel(output_test_result(i, result))
        console.print(panel)
        console.print(NewLine())
    if not report.title_prefix:
        table = Table()
        table.add_column("Grade")
        table.add_column("Total")
        table.add_row("%.1f" % report.grade, "%.1f" % report.total)
        console.print(table)

def file_console(f, **options):
    return Console(file=f, force_jupyter=False, force_terminal=False, **options)


def check(title_prefix: str = ""):
    if not os.path.exists('./expected.json'):
        print("Not ready yet.")
        return

    if not os.path.exists('./worksheet.ipynb'):
        print("No worksheet.")
        return
    
    expected_list = open_expected('./expected.json')
    nb = open_notebook('./worksheet.ipynb')

    report = make_report(nb, expected_list, title_prefix)
    print_report(report)
