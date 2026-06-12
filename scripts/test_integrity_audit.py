#!/usr/bin/env python3
"""
Test Integrity Audit Script.
Prevents false-confidence from "green builds" created by weakened tests.

Fails the CI build if:
- assert True
- assert .* is not None
- assert len(.*) > 0
- Tests containing only `pass`
- Tests containing only `return`
"""

import sys
import re
from pathlib import Path

def audit_tests():
    test_dir = Path("tests")
    if not test_dir.exists():
        print("Tests directory not found.")
        return 1

    weak_patterns = [
        re.compile(r"^\s*assert True\s*$"),
        re.compile(r"^\s*assert \w+ is not None\s*$"),
        re.compile(r"^\s*assert len\([^)]+\) > 0\s*$"),
    ]

    pass_pattern = re.compile(r"^\s*pass\s*$")
    return_pattern = re.compile(r"^\s*return\s*$")
    skip_pattern = re.compile(r"@pytest\.mark\.skip")
    xfail_pattern = re.compile(r"@pytest\.mark\.xfail")

    weak_tests = 0
    skipped_tests = 0
    real_tests = 0

    for test_file in test_dir.rglob("test_*.py"):
        with open(test_file, "r") as f:
            lines = f.readlines()
        
        in_test = False
        test_body = []
        test_name = ""

        for i, line in enumerate(lines):
            if "@pytest.mark.skip" in line:
                skipped_tests += 1
            if "@pytest.mark.xfail" in line:
                skipped_tests += 1

            if line.strip().startswith("def test_"):
                if in_test:
                    # process previous test
                    weak_tests += check_test_body(test_name, test_body, weak_patterns, pass_pattern, return_pattern, str(test_file))
                    real_tests += 1
                in_test = True
                test_name = line.strip().split("(")[0].replace("def ", "")
                test_body = []
            elif in_test and (line.startswith("def ") or line.startswith("class ")):
                # Test ended
                weak_tests += check_test_body(test_name, test_body, weak_patterns, pass_pattern, return_pattern, str(test_file))
                real_tests += 1
                in_test = False
                test_name = ""
                test_body = []
            elif in_test:
                test_body.append(line)
        
        # Process the last test in file
        if in_test:
            weak_tests += check_test_body(test_name, test_body, weak_patterns, pass_pattern, return_pattern, str(test_file))
            real_tests += 1

    print("\n--- Test Integrity Audit Results ---")
    print(f"REAL_TESTS: {real_tests}")
    print(f"WEAK_TESTS: {weak_tests}")
    print(f"SKIPPED_TESTS: {skipped_tests}")

    if weak_tests > 0:
        print("\n❌ FAILED: Found weakened tests. You must write behavior-validating assertions.")
        return 1
    
    print("\n✅ PASSED: Test suite integrity is strong.")
    return 0

def check_test_body(name, body, weak_patterns, pass_pattern, return_pattern, filename):
    body_text = "".join(body).strip()
    
    # Check for empty/pass/return tests
    lines = [l.strip() for l in body if l.strip() and not l.strip().startswith("#")]
    if len(lines) == 1 and pass_pattern.match(lines[0]):
        print(f"Weak test found [{filename}::{name}]: contains only 'pass'")
        return 1
    if len(lines) == 1 and return_pattern.match(lines[0]):
        print(f"Weak test found [{filename}::{name}]: contains only 'return'")
        return 1

    weak = 0
    for line in body:
        for p in weak_patterns:
            if p.match(line):
                print(f"Weak assertion found [{filename}::{name}]: {line.strip()}")
                weak += 1
    return weak

if __name__ == "__main__":
    sys.exit(audit_tests())
