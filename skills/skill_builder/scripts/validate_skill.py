#!/usr/bin/env python3
"""
Validate an Agent Skill directory structure and SKILL.md format.

Usage:
    python validate_skill.py <skill-directory>
    python validate_skill.py <skill-directory> --verbose
    python validate_skill.py <skill-directory> --strict

Examples:
    python validate_skill.py ./my-skill
    python validate_skill.py ./my-skill --verbose
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple, Optional


class ValidationResult:
    """Container for validation results."""
    
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.info: List[str] = []
    
    def add_error(self, message: str):
        self.errors.append(f"❌ ERROR: {message}")
    
    def add_warning(self, message: str):
        self.warnings.append(f"⚠️  WARNING: {message}")
    
    def add_info(self, message: str):
        self.info.append(f"ℹ️  INFO: {message}")
    
    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0
    
    def print_results(self, verbose: bool = False):
        if self.errors:
            print("\n".join(self.errors))
        if self.warnings:
            print("\n".join(self.warnings))
        if verbose and self.info:
            print("\n".join(self.info))
        
        print()
        if self.is_valid:
            print("✅ Skill validation PASSED")
            if self.warnings:
                print(f"   ({len(self.warnings)} warning(s))")
        else:
            print(f"❌ Skill validation FAILED ({len(self.errors)} error(s))")


def parse_yaml_frontmatter(content: str) -> Tuple[Optional[dict], str]:
    """
    Parse YAML frontmatter from SKILL.md content.
    
    Returns:
        Tuple of (frontmatter_dict, markdown_body)
    """
    if not content.startswith('---'):
        return None, content
    
    # Find the closing ---
    lines = content.split('\n')
    end_index = -1
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == '---':
            end_index = i
            break
    
    if end_index == -1:
        return None, content
    
    frontmatter_text = '\n'.join(lines[1:end_index])
    body = '\n'.join(lines[end_index + 1:])
    
    # Simple YAML parsing for required fields
    frontmatter = {}
    for line in frontmatter_text.split('\n'):
        if ':' in line and not line.strip().startswith('#'):
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip().strip('"\'')
            if key and value:
                frontmatter[key] = value
    
    return frontmatter, body


def validate_skill_name(name: str) -> List[str]:
    """Validate the skill name field."""
    errors = []
    
    if not name:
        errors.append("name field is empty")
        return errors
    
    if len(name) > 64:
        errors.append(f"name exceeds 64 characters ({len(name)} chars)")
    
    if not re.match(r'^[a-z][a-z0-9-]*$', name):
        errors.append("name must start with lowercase letter, contain only lowercase letters, numbers, and hyphens")
    
    if name.endswith('-'):
        errors.append("name must not end with a hyphen")
    
    if '--' in name:
        errors.append("name must not contain consecutive hyphens")
    
    if '<' in name or '>' in name:
        errors.append("name must not contain XML tags")
    
    reserved_words = {'skill', 'skills', 'claude', 'anthropic', 'system'}
    if name.lower() in reserved_words:
        errors.append(f"name '{name}' is a reserved word")
    
    return errors


def validate_description(description: str) -> List[str]:
    """Validate the description field."""
    errors = []
    warnings = []
    
    if not description:
        errors.append("description field is empty")
        return errors, warnings
    
    if len(description) > 1024:
        errors.append(f"description exceeds 1024 characters ({len(description)} chars)")
    
    if '<' in description and '>' in description:
        errors.append("description must not contain XML tags")
    
    # Check for vague descriptions
    vague_patterns = [
        r'\bhelps?\b',
        r'\bvarious\b',
        r'\bstuff\b',
        r'\bthings?\b',
    ]
    for pattern in vague_patterns:
        if re.search(pattern, description.lower()):
            warnings.append(f"description may be too vague (contains '{pattern.strip(chr(92)).strip('b')}')")
            break
    
    # Check for trigger keywords
    if len(description) < 50:
        warnings.append("description is very short; consider adding trigger keywords")
    
    return errors, warnings


def validate_skill_directory(skill_path: Path, strict: bool = False) -> ValidationResult:
    """
    Validate a skill directory.
    
    Args:
        skill_path: Path to the skill directory
        strict: If True, treat warnings as errors
        
    Returns:
        ValidationResult with all findings
    """
    result = ValidationResult()
    
    # Check directory exists
    if not skill_path.exists():
        result.add_error(f"Directory does not exist: {skill_path}")
        return result
    
    if not skill_path.is_dir():
        result.add_error(f"Path is not a directory: {skill_path}")
        return result
    
    result.add_info(f"Validating skill at: {skill_path.absolute()}")
    
    # Check SKILL.md exists
    skill_md_path = skill_path / "SKILL.md"
    if not skill_md_path.exists():
        # Try case-insensitive search
        skill_files = list(skill_path.glob("*.md"))
        skill_file_names = [f.name.lower() for f in skill_files]
        if "skill.md" in skill_file_names:
            result.add_warning("SKILL.md has incorrect case (must be uppercase)")
        else:
            result.add_error("SKILL.md file not found")
            return result
    
    # Read and parse SKILL.md
    try:
        content = skill_md_path.read_text(encoding='utf-8')
    except Exception as e:
        result.add_error(f"Cannot read SKILL.md: {e}")
        return result
    
    result.add_info(f"SKILL.md size: {len(content)} characters (~{len(content)//4} tokens)")
    
    # Check frontmatter starts at line 1
    if not content.startswith('---'):
        result.add_error("SKILL.md must start with '---' (YAML frontmatter)")
        return result
    
    # Parse frontmatter
    frontmatter, body = parse_yaml_frontmatter(content)
    
    if frontmatter is None:
        result.add_error("Invalid YAML frontmatter (missing closing '---')")
        return result
    
    # Validate name field
    name = frontmatter.get('name', '')
    if not name:
        result.add_error("Missing required field: name")
    else:
        name_errors = validate_skill_name(name)
        for err in name_errors:
            result.add_error(f"name: {err}")
        result.add_info(f"Skill name: {name}")
    
    # Validate description field
    description = frontmatter.get('description', '')
    if not description:
        result.add_error("Missing required field: description")
    else:
        desc_errors, desc_warnings = validate_description(description)
        for err in desc_errors:
            result.add_error(f"description: {err}")
        for warn in desc_warnings:
            if strict:
                result.add_error(f"description: {warn}")
            else:
                result.add_warning(f"description: {warn}")
        result.add_info(f"Description length: {len(description)} chars")
    
    # Check body content
    body_lines = [l for l in body.strip().split('\n') if l.strip()]
    if len(body_lines) == 0:
        result.add_warning("SKILL.md body is empty")
    elif len(body_lines) > 500:
        if strict:
            result.add_error(f"SKILL.md body exceeds 500 lines ({len(body_lines)} lines)")
        else:
            result.add_warning(f"SKILL.md body exceeds recommended 500 lines ({len(body_lines)} lines)")
    
    result.add_info(f"Body: {len(body_lines)} lines")
    
    # Check for referenced files
    referenced_files = []
    for match in re.finditer(r'`((?:scripts|references|assets)/[^`]+)`', body):
        referenced_files.append(match.group(1))
    
    for match in re.finditer(r'\[.*?\]\(((?:scripts|references|assets)/[^\)]+)\)', body):
        referenced_files.append(match.group(1))
    
    for ref_file in set(referenced_files):
        ref_path = skill_path / ref_file
        if not ref_path.exists():
            result.add_warning(f"Referenced file not found: {ref_file}")
        else:
            result.add_info(f"Referenced file exists: {ref_file}")
    
    # Check optional directories
    for dir_name in ['scripts', 'references', 'assets']:
        dir_path = skill_path / dir_name
        if dir_path.exists():
            files = list(dir_path.glob('*'))
            files = [f for f in files if f.name != '.gitkeep']
            result.add_info(f"{dir_name}/: {len(files)} file(s)")
            
            # Check for Python syntax errors in scripts
            if dir_name == 'scripts':
                for py_file in dir_path.glob('*.py'):
                    try:
                        with open(py_file) as f:
                            compile(f.read(), py_file, 'exec')
                        result.add_info(f"  {py_file.name}: syntax OK")
                    except SyntaxError as e:
                        result.add_error(f"Syntax error in {py_file.name}: {e}")
    
    # Check for common issues
    if '[REQUIRED]' in content or '[your-name]' in content:
        result.add_warning("SKILL.md contains placeholder text that should be replaced")
    
    # Check total token estimate
    total_chars = len(content)
    for dir_name in ['scripts', 'references', 'assets']:
        dir_path = skill_path / dir_name
        if dir_path.exists():
            for f in dir_path.rglob('*'):
                if f.is_file() and f.suffix in ['.md', '.txt', '.py', '.sh', '.js']:
                    total_chars += f.stat().st_size
    
    result.add_info(f"Total skill content: ~{total_chars // 4} tokens")
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Validate an Agent Skill directory",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "skill_directory",
        help="Path to the skill directory to validate"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed information messages"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors"
    )
    
    args = parser.parse_args()
    
    skill_path = Path(args.skill_directory)
    result = validate_skill_directory(skill_path, strict=args.strict)
    result.print_results(verbose=args.verbose)
    
    sys.exit(0 if result.is_valid else 1)


if __name__ == "__main__":
    main()
