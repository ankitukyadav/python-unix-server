"""
Basic SSH Connection and Command Execution
This module demonstrates fundamental SSH connectivity using paramiko library
and showcases Python networking concepts, error handling, and context managers.
"""

import paramiko
import socket
import time
import logging
from typing import Optional, Dict, List, Tuple, Any
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import getpass
import json

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class ServerConfig:
    """Configuration class for server connection details."""
    hostname: str
    username: str
    password: Optional[str] = None
    private_key_path: Optional[str] = None
    port: int = 22
    timeout: int = 30
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        if not self.password and not self.private_key_path:
            raise ValueError("Either password or private key path must be provided")

@dataclass
class CommandResult:
    """Data class to store command execution results."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    execution_time: float
    success: bool
    
    def __str__(self) -> str:
        status = "SUCCESS" if self.success else "FAILED"
        return f"Command: {self.command}\nStatus: {status}\nExit Code: {exit_code}\nExecution Time: {self.execution_time:.2f}s"

class SSHConnectionError(Exception):
    """Custom exception for SSH connection errors."""
    pass

class CommandExecutionError(Exception):
    """Custom exception for command execution errors."""
    pass

class BasicSSHClient:
    """
    Basic SSH client for connecting to Unix servers and executing commands.
    Demonstrates Python concepts: context managers, error handling, logging.
    """
    
    def __init__(self, config: ServerConfig):
        """
        Initialize SSH client with server configuration.
        
        Args:
            config: ServerConfig object containing connection details
        """
        self.config = config
        self.client: Optional[paramiko.SSHClient] = None
        self.connected = False
        
    def __enter__(self):
        """Context manager entry - establish connection."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup connection."""
        self.disconnect()
    
    def connect(self) -> None:
        """
        Establish SSH connection to the server.
        
        Raises:
            SSHConnectionError: If connection fails
        """
        try:
            self.client = paramiko.SSHClient()
            
            # Set policy for unknown hosts (use with caution in production)
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Prepare connection parameters
            connect_kwargs = {
                'hostname': self.config.hostname,
                'username': self.config.username,
                'port': self.config.port,
                'timeout': self.config.timeout
            }
            
            # Add authentication method
            if self.config.private_key_path:
                private_key = paramiko.RSAKey.from_private_key_file(self.config.private_key_path)
                connect_kwargs['pkey'] = private_key
                logger.info(f"Using private key authentication for {self.config.username}@{self.config.hostname}")
            else:
                connect_kwargs['password'] = self.config.password
                logger.info(f"Using password authentication for {self.config.username}@{self.config.hostname}")
            
            # Establish connection
            self.client.connect(**connect_kwargs)
            self.connected = True
            logger.info(f"Successfully connected to {self.config.hostname}:{self.config.port}")
            
        except paramiko.AuthenticationException as e:
            raise SSHConnectionError(f"Authentication failed: {e}")
        except paramiko.SSHException as e:
            raise SSHConnectionError(f"SSH connection error: {e}")
        except socket.timeout as e:
            raise SSHConnectionError(f"Connection timeout: {e}")
        except Exception as e:
            raise SSHConnectionError(f"Unexpected connection error: {e}")
    
    def disconnect(self) -> None:
        """Close SSH connection."""
        if self.client:
            self.client.close()
            self.connected = False
            logger.info(f"Disconnected from {self.config.hostname}")
    
    def execute_command(self, command: str, timeout: Optional[int] = None) -> CommandResult:
        """
        Execute a single command on the remote server.
        
        Args:
            command: Command to execute
            timeout: Command timeout in seconds
            
        Returns:
            CommandResult object with execution details
            
        Raises:
            CommandExecutionError: If command execution fails
        """
        if not self.connected or not self.client:
            raise CommandExecutionError("Not connected to server")
        
        try:
            start_time = time.time()
            logger.info(f"Executing command: {command}")
            
            # Execute command
            stdin, stdout, stderr = self.client.exec_command(
                command, 
                timeout=timeout or self.config.timeout
            )
            
            # Read output
            exit_code = stdout.channel.recv_exit_status()
            stdout_data = stdout.read().decode('utf-8').strip()
            stderr_data = stderr.read().decode('utf-8').strip()
            
            execution_time = time.time() - start_time
            success = exit_code == 0
            
            result = CommandResult(
                command=command,
                exit_code=exit_code,
                stdout=stdout_data,
                stderr=stderr_data,
                execution_time=execution_time,
                success=success
            )
            
            if success:
                logger.info(f"Command completed successfully in {execution_time:.2f}s")
            else:
                logger.warning(f"Command failed with exit code {exit_code}")
            
            return result
            
        except socket.timeout:
            raise CommandExecutionError(f"Command timeout: {command}")
        except Exception as e:
            raise CommandExecutionError(f"Command execution failed: {e}")
    
    def execute_multiple_commands(self, commands: List[str], 
                                 stop_on_error: bool = True) -> List[CommandResult]:
        """
        Execute multiple commands sequentially.
        
        Args:
            commands: List of commands to execute
            stop_on_error: Whether to stop execution on first error
            
        Returns:
            List of CommandResult objects
        """
        results = []
        
        for command in commands:
            try:
                result = self.execute_command(command)
                results.append(result)
                
                if not result.success and stop_on_error:
                    logger.error(f"Stopping execution due to failed command: {command}")
                    break
                    
            except CommandExecutionError as e:
                logger.error(f"Failed to execute command '{command}': {e}")
                if stop_on_error:
                    break
        
        return results
    
    def test_connection(self) -> bool:
        """
        Test the SSH connection by executing a simple command.
        
        Returns:
            True if connection is working, False otherwise
        """
        try:
            result = self.execute_command("echo 'Connection test successful'")
            return result.success and "Connection test successful" in result.stdout
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

# Utility functions for common operations
def create_server_config_interactive() -> ServerConfig:
    """
    Create server configuration interactively.
    Demonstrates input validation and secure password handling.
    """
    print("=== SSH Server Configuration ===")
    
    hostname = input("Enter hostname or IP address: ").strip()
    if not hostname:
        raise ValueError("Hostname cannot be empty")
    
    username = input("Enter username: ").strip()
    if not username:
        raise ValueError("Username cannot be empty")
    
    port = input("Enter port (default 22): ").strip()
    port = int(port) if port else 22
    
    auth_method = input("Authentication method (1: Password, 2: Private Key): ").strip()
    
    password = None
    private_key_path = None
    
    if auth_method == "1":
        password = getpass.getpass("Enter password: ")
    elif auth_method == "2":
        private_key_path = input("Enter private key path: ").strip()
        if not Path(private_key_path).exists():
            raise FileNotFoundError(f"Private key file not found: {private_key_path}")
    else:
        raise ValueError("Invalid authentication method")
    
    return ServerConfig(
        hostname=hostname,
        username=username,
        password=password,
        private_key_path=private_key_path,
        port=port
    )

def save_server_config(config: ServerConfig, filename: str) -> None:
    """
    Save server configuration to JSON file (excluding sensitive data).
    
    Args:
        config: ServerConfig object
        filename: Output filename
    """
    config_dict = {
        'hostname': config.hostname,
        'username': config.username,
        'port': config.port,
        'timeout': config.timeout,
        'private_key_path': config.private_key_path
        # Note: Password is intentionally excluded for security
    }
    
    with open(filename, 'w') as f:
        json.dump(config_dict, f, indent=2)
    
    logger.info(f"Configuration saved to {filename}")

def load_server_config(filename: str) -> ServerConfig:
    """
    Load server configuration from JSON file.
    
    Args:
        filename: Input filename
        
    Returns:
        ServerConfig object
    """
    with open(filename, 'r') as f:
        config_dict = json.load(f)
    
    # Prompt for password if not using private key
    if not config_dict.get('private_key_path'):
        config_dict['password'] = getpass.getpass(f"Enter password for {config_dict['username']}: ")
    
    return ServerConfig(**config_dict)

# Example usage and demonstrations
def demonstrate_basic_connection():
    """Demonstrate basic SSH connection and command execution."""
    print("=== Basic SSH Connection Demo ===")
    
    # Create configuration (in real usage, you'd get this from user input or config file)
    config = ServerConfig(
        hostname="example.com",  # Replace with actual server
        username="your_username",  # Replace with actual username
        password="your_password",  # Replace with actual password or use private key
        port=22
    )
    
    try:
        # Using context manager ensures proper cleanup
        with BasicSSHClient(config) as ssh:
            # Test connection
            if ssh.test_connection():
                print("✓ Connection test successful")
            else:
                print("✗ Connection test failed")
                return
            
            # Execute single command
            result = ssh.execute_command("uname -a")
            print(f"System info: {result.stdout}")
            
            # Execute multiple commands
            commands = [
                "whoami",
                "pwd",
                "ls -la",
                "df -h"
            ]
            
            results = ssh.execute_multiple_commands(commands)
            
            for result in results:
                print(f"\nCommand: {result.command}")
                print(f"Output: {result.stdout}")
                if result.stderr:
                    print(f"Error: {result.stderr}")
                print(f"Execution time: {result.execution_time:.2f}s")
    
    except SSHConnectionError as e:
        logger.error(f"Connection failed: {e}")
    except CommandExecutionError as e:
        logger.error(f"Command execution failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

def demonstrate_interactive_setup():
    """Demonstrate interactive server configuration setup."""
    print("=== Interactive Setup Demo ===")
    
    try:
        # Create configuration interactively
        config = create_server_config_interactive()
        
        # Save configuration
        save_server_config(config, "server_config.json")
        
        # Test connection
        with BasicSSHClient(config) as ssh:
            if ssh.test_connection():
                print("✓ Configuration is valid and connection successful")
                
                # Execute a test command
                result = ssh.execute_command("date")
                print(f"Server time: {result.stdout}")
            else:
                print("✗ Configuration test failed")
    
    except Exception as e:
        logger.error(f"Interactive setup failed: {e}")

def main():
    """Main demonstration function."""
    print("=== Unix Server Connection Examples ===")
    
    # Note: These demonstrations require actual server credentials
    # Uncomment and modify as needed for testing
    
    print("\n1. Basic Connection Demo")
    print("   (Requires server credentials - modify demonstrate_basic_connection())")
    # demonstrate_basic_connection()
    
    print("\n2. Interactive Setup Demo")
    print("   (Requires user input - uncomment to test)")
    # demonstrate_interactive_setup()
    
    print("\n3. Configuration Management Demo")
    # Example of creating and saving configuration
    example_config = ServerConfig(
        hostname="example.com",
        username="user",
        password="password",
        port=22
    )
    
    save_server_config(example_config, "example_config.json")
    print("✓ Example configuration saved to example_config.json")
    
    print("\nTo use these examples:")
    print("1. Install paramiko: pip install paramiko")
    print("2. Modify server credentials in the demo functions")
    print("3. Run the appropriate demonstration function")

if __name__ == "__main__":
    main()
