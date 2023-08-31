import lambdacheck.cli as cli
import sys

if __name__ == '__main__':
    if sys.argv[1:]:
        cli.λCheck(student_name=sys.argv[1])
    else:
        cli.λCheck()
