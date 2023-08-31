import os
import sys
import io
from contextlib import redirect_stdout
import shutil
import re
import rich
from rich.console import Console, NewLine
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from lambdacheck import *
import pandas as pd

def Hello():
    table = Table()
    table.add_column("Function")
    table.add_column("Description")
    table.add_row("Generate", "generate worksheet.ipynb checks.json tests.json")
    table.add_row("Check", "checks <title_prefix> with test=False|True")
    rich.print(table)

def Generate():
    try:
        master = open_notebook('master.ipynb')
        worksheet = make_worksheet(master)
        expected_check = make_expected_list(master, test=False)
        expected_test = make_expected_list(master, test=True)

        rich.print("> worksheet.ipynb")
        write_notebook(worksheet, 'worksheet.ipynb')

        rich.print("> checks.json")
        write_expected(expected_check, 'checks.json')

        rich.print("> tests.json")
        write_expected(expected_test, 'tests.json')
    except Exception as e:
        print("Error:", file=sys.stderr)
        print(e, file=sys.stderr)


def Check(title_prefix: str = "", 
        expected_file="checks.json", 
        notebook='worksheet.ipynb', 
        report_path=None,
        student_name=None):
    try:
        expected_list = open_expected(expected_file)
        nb = open_notebook(notebook)
        report = make_report(nb, expected_list, title_prefix)
        if student_name:
            report_file = os.path.join(report_path, "{}.report".format(student_name))
            with open(report_file, 'w') as f:
                print_report(report, console=file_console(f))
            print("%s,%s,%s" % (student_name, report.grade, report.total))
        else:
            print_report(report)
    except Exception as e:
        print("Error:", file=sys.stderr)
        print(e, file=sys.stderr)

def Test(student_name):
    Check(expected_file='tests.json', student_name=student_name)
