#!/usr/bin/env python3
"""
Initialize a new Agent Skill directory with proper structure.

Usage:
    python init_skill.py <skill-name> [--path <output-path>] [--resources <dirs>] [--examples]

Examples:
    python init_skill.py my-skill                                    # Creates ~/.claude/skills/my-skill/
    python init_skill.py my-skill --path ./skills                    # Creates ./skills/my-skill/
    python init_skill.py my-skill --resources scripts,references
    python init_skill.py my-skill --resources scripts,references,assets --examples
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path


def validate_skill_name(name: str) -> bool:
    """Validate skill name follows conventions."""
    pattern = r'^[a-z][a-z0-9-]*[a-z0-9]$|^[a-z]$'
    if len(name) > 64:
        return False
    if not re.match(pattern, name):
        return False
    if '--' in name:
        return False
    return True


def create_skill_md(skill_name: str, include_examples: bool = False) -> str:
    """Generate SKILL.md content."""
    content = f'''---
name: {skill_name}
description: [REQUIRED] Describe what this skill does and when to use it. Include specific trigger keywords.
license: Apache-2.0
metadata:
  author: [your-name]
  version: "1.0"
  created: "{datetime.now().strftime('%Y-%m-%d')}"
---

# {skill_name.replace('-', ' ').title()}

[Brief description of the skill's purpose - 2-3 sentences]

## Overview

[Explain what this skill does and the problem it solves]

## Quick Start

[Minimal example showing the fastest path to results]

```python
# Example code
```

## Instructions

### Task Type 1

[Step-by-step instructions for this task type]

1. First step
2. Second step
3. Third step

### Task Type 2

[Step-by-step instructions for another task type]

## Examples

### Example 1: [Name]

**Input:** [What the user provides]

**Output:** [What the skill produces]

## Edge Cases

- **[Edge case 1]**: How to handle it
- **[Edge case 2]**: How to handle it

## References

[List any bundled files that can be loaded on demand]

- See `references/[file].md` for [description]
- Run `scripts/[script].py` for [purpose]
'''
    return content


def create_script_example() -> str:
    """Generate example script content."""
    return '''#!/usr/bin/env python3
"""
Example utility script for the skill.

Usage:
    python example_script.py <input> [--option value]

This script demonstrates the pattern for bundled scripts.
Scripts should be:
- Self-contained or clearly document dependencies
- Include usage examples in docstrings
- Handle edge cases gracefully
- Tested before bundling
"""

import argparse
import sys


def main(input_value: str, option: str = "default") -> dict:
    """
    Main function that performs the script's operation.
    
    Args:
        input_value: The primary input to process
        option: Optional parameter with default value
        
    Returns:
        Dictionary with results
    """
    # Example processing logic
    result = {
        "input": input_value,
        "option": option,
        "processed": True,
        "output": f"Processed: {input_value}"
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Example utility script")
    parser.add_argument("input", help="Input value to process")
    parser.add_argument("--option", default="default", help="Optional parameter")
    
    args = parser.parse_args()
    
    try:
        result = main(args.input, args.option)
        print(f"Success: {result}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
'''


def create_reference_example() -> str:
    """Generate example reference content."""
    return '''# Reference Documentation

This file contains additional documentation that can be loaded on demand.

## Table of Contents
1. [Section 1](#section-1)
2. [Section 2](#section-2)
3. [Section 3](#section-3)

---

## Section 1

[Detailed documentation for section 1]

### Subsection 1.1

[More specific details]

### Subsection 1.2

[Additional details]

---

## Section 2

[Detailed documentation for section 2]

---

## Section 3

[Detailed documentation for section 3]

---

## Quick Reference

| Term | Definition |
|------|------------|
| Term 1 | Definition 1 |
| Term 2 | Definition 2 |
| Term 3 | Definition 3 |
'''


def create_asset_readme() -> str:
    """Generate README for assets directory."""
    return '''# Assets Directory

This directory contains static files used by the skill:

- Templates (HTML, JSON, YAML, etc.)
- Images and icons
- Fonts
- Configuration defaults
- Sample data files

## Guidelines

- Keep binary files minimal
- Prefer text formats when possible
- Organize files by type or purpose
- Document file purposes in this README

## Contents

[List your asset files here]

- `template.json` - [Description]
- `example.html` - [Description]
'''


def init_skill(
    skill_name: str,
    output_path: str = ".",
    resources: list = None,
    include_examples: bool = False
) -> Path:
    """
    Initialize a new skill directory.
    
    Args:
        skill_name: Name of the skill (lowercase, hyphens allowed)
        output_path: Parent directory for the skill
        resources: List of resource directories to create
        include_examples: Whether to include example files
        
    Returns:
        Path to created skill directory
    """
    if not validate_skill_name(skill_name):
        raise ValueError(
            f"Invalid skill name: '{skill_name}'. "
            "Use lowercase letters, numbers, and hyphens. Max 64 chars. "
            "Must start with letter, not end with hyphen."
        )
    
    # Default resources if none specified
    if resources is None:
        resources = []
    
    # Create skill directory
    skill_dir = Path(output_path) / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    
    # Create SKILL.md
    skill_md_path = skill_dir / "SKILL.md"
    skill_md_path.write_text(create_skill_md(skill_name, include_examples))
    print(f"Created: {skill_md_path}")
    
    # Create resource directories
    for resource in resources:
        resource_dir = skill_dir / resource
        resource_dir.mkdir(exist_ok=True)
        
        if include_examples:
            if resource == "scripts":
                example_script = resource_dir / "example_script.py"
                example_script.write_text(create_script_example())
                print(f"Created: {example_script}")
            elif resource == "references":
                example_ref = resource_dir / "example_reference.md"
                example_ref.write_text(create_reference_example())
                print(f"Created: {example_ref}")
            elif resource == "assets":
                readme = resource_dir / "README.md"
                readme.write_text(create_asset_readme())
                print(f"Created: {readme}")
        else:
            # Create .gitkeep to preserve empty directories
            gitkeep = resource_dir / ".gitkeep"
            gitkeep.touch()
        
        print(f"Created directory: {resource_dir}")
    
    # Create LICENSE file
    license_path = skill_dir / "LICENSE"
    license_path.write_text("Apache-2.0\n\nSee: https://www.apache.org/licenses/LICENSE-2.0\n")
    print(f"Created: {license_path}")
    
    print(f"\n✓ Skill initialized at: {skill_dir.absolute()}")
    print("\nNext steps:")
    print("1. Edit SKILL.md to add your skill's instructions")
    print("2. Update the name and description in the YAML frontmatter")
    print("3. Add any scripts, references, or assets needed")
    print("4. Test the skill with representative tasks")
    
    return skill_dir


def main():
    parser = argparse.ArgumentParser(
        description="Initialize a new Agent Skill directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s my-skill
  %(prog)s my-skill --path ./skills
  %(prog)s my-skill --resources scripts,references
  %(prog)s my-skill --resources scripts,references,assets --examples
        """
    )
    parser.add_argument(
        "skill_name",
        help="Name of the skill (lowercase, hyphens allowed, max 64 chars)"
    )
    parser.add_argument(
        "--path",
        default=os.path.expanduser("~/.claude/skills"),
        help="Output path for the skill directory (default: ~/.claude/skills/)"
    )
    parser.add_argument(
        "--resources",
        default="",
        help="Comma-separated list of resource directories: scripts,references,assets"
    )
    parser.add_argument(
        "--examples",
        action="store_true",
        help="Include example files in resource directories"
    )
    
    args = parser.parse_args()
    
    # Parse resources
    resources = [r.strip() for r in args.resources.split(",") if r.strip()]
    valid_resources = {"scripts", "references", "assets"}
    for r in resources:
        if r not in valid_resources:
            print(f"Warning: Unknown resource type '{r}'. Valid: {valid_resources}")
    resources = [r for r in resources if r in valid_resources]
    
    try:
        init_skill(
            skill_name=args.skill_name,
            output_path=args.path,
            resources=resources,
            include_examples=args.examples
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
