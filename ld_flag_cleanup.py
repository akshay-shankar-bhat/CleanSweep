import ast
import os
import argparse
import shutil
import re
from typing import Tuple, List

class LocationCollector(ast.NodeVisitor):
    """Collects the locations of feature flag blocks in the code"""
    def __init__(self, feature_flag_name):
        self.feature_flag_name = feature_flag_name
        self.locations = []

    def visit_If(self, node):
        """Visit If nodes to find feature flag conditions"""
        # Check if this is a feature flag condition (either direct or negated)
        is_feature_flag = False
        keep_body = True  # Default to keeping the true branch
        
        if isinstance(node.test, ast.Call):
            # Direct feature flag call
            if (isinstance(node.test.func, ast.Attribute) and 
                node.test.func.attr == self.feature_flag_name):
                is_feature_flag = True
        elif isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not):
            # Negated feature flag call
            if (isinstance(node.test.operand, ast.Call) and 
                isinstance(node.test.operand.func, ast.Attribute) and 
                node.test.operand.func.attr == self.feature_flag_name):
                is_feature_flag = True
                keep_body = False  # For negated conditions, we want to remove the body
        
        if is_feature_flag:
            if_start = node.lineno - 1
            if_end = node.end_lineno
            
            if keep_body:
                # Store the true branch for regular feature flag checks
                true_branch_start = min(stmt.lineno - 1 for stmt in node.body)
                true_branch_end = max(stmt.end_lineno for stmt in node.body)
                self.locations.append({
                    'if_start': if_start,
                    'if_end': if_end,
                    'true_branch_start': true_branch_start,
                    'true_branch_end': true_branch_end,
                    'body': node.body,
                    'keep_body': True
                })
            else:
                # For negated conditions with raise, remove the entire block
                self.locations.append({
                    'if_start': if_start,
                    'if_end': if_end,
                    'keep_body': False
                })
            
        self.generic_visit(node)    
    
def get_indent(line: str) -> str:
    """Extract the indentation from a line of code"""
    return line[:len(line) - len(line.lstrip())]

def adjust_indentation(lines: List[str], target_indent: str) -> List[str]:
    """Adjust indentation of a block of code to match target indentation"""
    if not lines:
        return lines
        
    # Find the minimum indentation in the block
    non_empty_lines = [line for line in lines if line.strip()]
    if not non_empty_lines:
        return lines
    current_indent = get_indent(non_empty_lines[0])
    min_indent_length = len(current_indent)
    
    # If the code block is already at the correct indentation, return as is
    if current_indent == target_indent:
        return lines
        
    adjusted_lines = []
    for line in lines:
        if line.strip():  # Only adjust non-empty lines
            # Remove the current indentation and add the target indentation
            stripped_line = line[min_indent_length:]
            adjusted_lines.append(target_indent + stripped_line)
        else:
            adjusted_lines.append(line)  # Keep empty lines as they are
            
    return adjusted_lines

def clean_flag_usage(source: str, feature_flag_name: str) -> Tuple[str, bool]:
    """Clean feature flags while preserving exact formatting"""
    try:
        # Parse the source to get AST
        tree = ast.parse(source)
        
        # Collect feature flag locations
        collector = LocationCollector(feature_flag_name)
        collector.visit(tree)
        
        if not collector.locations:
            return source, False
            
        # Split source into lines for processing
        lines = source.splitlines(keepends=True)
        
        # Process locations in reverse order to avoid invalidating line numbers
        for loc in reversed(collector.locations):
            if loc['keep_body']:
                # Get the indentation of the if statement
                if_indent = get_indent(lines[loc['if_start']])
                
                # Get the true branch lines
                true_branch_lines = lines[loc['true_branch_start']:loc['true_branch_end']]
                
                # Adjust the indentation of the true branch to match the if statement's indentation
                adjusted_lines = adjust_indentation(true_branch_lines, if_indent)
                
                # Replace the entire if-else block with the adjusted true branch
                lines[loc['if_start']:loc['if_end']] = adjusted_lines
            else:
                # For negated conditions with raise, remove the entire block
                lines[loc['if_start']:loc['if_end']] = []
            
        return ''.join(lines), True
        
    except Exception as e:
        print(f"Error cleaning source: {str(e)}")
        return source, False

def remove_flag_definition(content: str, flag_name: str) -> Tuple[str, bool]:
    """Remove a specific feature flag definition from the class"""
    lines = content.splitlines(keepends=True)
    modified = False
    output_lines = []
    
    # Pattern to match the feature flag definition
    flag_pattern = re.compile(rf'{flag_name}\s*=\s*FeatureFlag\([^)]*\)')
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Skip empty lines at the start
        if not line.strip():
            output_lines.append(line)
            i += 1
            continue
            
        # Check if this line contains the feature flag definition
        if flag_name in line:
            # Try to match the single-line pattern
            if re.search(flag_pattern, line):
                modified = True
                i += 1
                continue
                
            # If not single-line, look for multi-line definition
            if '=' in line and 'FeatureFlag(' in line:
                # Found start of multi-line definition
                full_definition = line
                nested_level = line.count('(') - line.count(')')
                i += 1
                
                # Keep reading lines until we find the matching closing parenthesis
                while i < len(lines) and nested_level > 0:
                    next_line = lines[i]
                    full_definition += next_line
                    nested_level += next_line.count('(') - next_line.count(')')
                    i += 1
                
                if re.search(flag_pattern, full_definition.replace('\n', '')):
                    modified = True
                    continue
                else:
                    output_lines.append(line)
                    i -= 1
            else:
                output_lines.append(line)
        else:
            output_lines.append(line)
        i += 1
    
    # Clean up any double blank lines that might have been created
    result = ''.join(output_lines)
    result = re.sub(r'\n\n\n+', '\n\n', result)
    
    return result, modified

def process_file(file_path: str, feature_flag_name: str, create_backup: bool = True) -> bool:
    """Process a single file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        modified = False
        
        # If this is a feature flags definition file
        if 'feature_flags' in file_path.lower():
            cleaned_source, was_modified = remove_flag_definition(source, feature_flag_name)
            modified = modified or was_modified
            
        # Clean up feature flag usage
        cleaned_source, was_modified = clean_flag_usage(source if not modified else cleaned_source, feature_flag_name)
        modified = modified or was_modified
        
        if modified:
            if create_backup:
                backup_path = file_path + '.bak'
                shutil.copy2(file_path, backup_path)
                print(f"Created backup: {backup_path}")
                
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(cleaned_source)
                
            print(f"Processed file: {file_path}")
            return True
            
        return False
        
    except Exception as e:
        print(f"Error processing {file_path}: {str(e)}")
        return False

def process_directory(directory: str, feature_flag_name: str, file_pattern: str = '*.py', 
                     create_backup: bool = True) -> Tuple[int, int]:
    """Process all files in directory recursively"""
    import fnmatch
    
    total_files = 0
    modified_files = 0
    
    for root, _, files in os.walk(directory):
        for filename in fnmatch.filter(files, file_pattern):
            file_path = os.path.join(root, filename)
            if process_file(file_path, feature_flag_name, create_backup):
                modified_files += 1
            total_files += 1
            
    return total_files, modified_files

def main():
    parser = argparse.ArgumentParser(description='Clean feature flags from Python files')
    parser.add_argument('directory', help='Directory to process')
    parser.add_argument('feature_flag', help='Name of feature flag to remove')
    parser.add_argument('--pattern', default='*.py', help='File pattern (default: *.py)')
    parser.add_argument('--no-backup', action='store_true', help='Do not create backup files')
    
    args = parser.parse_args()
    
    total, modified = process_directory(
        args.directory,
        args.feature_flag,
        args.pattern,
        not args.no_backup
    )
    
    print(f"\nProcessing complete:")
    print(f"Files processed: {total}")
    print(f"Files modified: {modified}")

if __name__ == '__main__':
    main()
