# AWS Credentials Management

## Overview

This module implements an automatic AWS credentials refresh mechanism that solves the `UnrecognizedClientException` error when calling the AWS Bedrock InvokeModel operation.

## Problem

When using temporary AWS credentials (such as from IAM roles or session tokens), these credentials can expire during a session. When this happens, AWS API calls fail with an `UnrecognizedClientException` and an error message like "The security token included in the request is invalid."

## Solution

The implementation includes:

1. **Credential Manager** (`aws_credentials.py`): A dedicated class that manages AWS credentials, checks their validity, and refreshes them when needed.

2. **Credential Refresh Decorator** (`with_aws_credential_refresh`): Applied to methods that make AWS API calls to automatically detect and handle credential refresh when needed.

3. **Enhanced Error Handling**: Clear error messages that help diagnose authentication issues and provide user-friendly instructions.

## Implementation Details

### 1. Credential Manager Class

The `CredentialManager` class in `aws_credentials.py` provides the following features:

- Automatic initialization of AWS credentials using environment variables or AWS credential chains
- Periodic checking of credential validity
- Forced credential refresh when needed
- Retry mechanisms for failed API calls due to invalid credentials

### 2. Credential Refresh Decorator

The `@with_aws_credential_refresh` decorator is applied to methods that make AWS API calls. This decorator:

- Catches authentication errors like `UnrecognizedClientException`
- Automatically refreshes AWS credentials when they expire
- Recreates AWS clients with the new credentials
- Provides clear error messages

### 3. Enhanced Error Handling

Improved error messages that:

- Clearly indicate when AWS credentials have expired
- Explain how to resolve the issue
- Provide sufficient debugging information in logs

## Usage

The credential refresh mechanism works automatically. The key components are:

1. Initialization:
```python
from .aws_credentials import CredentialManager
# Initialize once in the constructor
self.credential_manager = CredentialManager(region_name=aws_region)
```

2. Protection:
```python
from .aws_credentials import with_aws_credential_refresh
# Apply to methods that make AWS API calls
@with_aws_credential_refresh
def some_aws_method(self):
    # Method implementation
```

3. Checks:
```python
# Check credential validity before making API calls
if self.credential_manager:
    self.credential_manager.check_credentials()
```

## Files Modified

- `src/claude3_video_analyzer/__init__.py`: Added credential manager integration
- `src/claude3_video_analyzer/aws_credentials.py`: New file with credential management implementation

## Methods Protected

The following methods now have automatic credential refresh:

- `VideoAnalyzer.analyze_video`
- `VideoAnalyzer.analyze_video_with_chapters`
- `ScriptGenerator.generate_script_for_chapter`
- `ScriptGenerator.analyze_script_quality` 
- `ScriptGenerator.improve_script`

## Benefits

1. **Resilience**: The application automatically recovers from expired AWS credentials without user intervention
2. **Better User Experience**: Clear error messages when credential issues can't be automatically resolved
3. **Reduced Support Burden**: Fewer crashes due to authentication issues