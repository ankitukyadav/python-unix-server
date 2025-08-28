"""
Advanced SSH Operations and File Management
This module demonstrates advanced SSH operations including file transfers,
tunneling, and batch operations. Showcases Python concepts like generators,
async operations, and advanced error handling.
"""

import paramiko
import scp
import os
import stat
import threading
import queue
import time
import hashlib
from typing import Generator, Iterator, Callable, Optional, Dict, List, Any
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import fnmatch
import tarfile
import gzip
from contextlib import contextmanager

from basic_ssh_connection import BasicSSHClient, ServerConfig, CommandResult, logger

@dataclass
class FileTransferResult:
    """Result of file transfer operation."""
    local_path: str
    remote_path: str
    size_bytes: int
    transfer_time: float
    success: bool
    error_message: Optional[str] = None
    
    @property
    def transfer_rate_mbps(self) -> float:
        """Calculate transfer rate in MB/s."""
        if self.transfer_time > 0:
            return (self.size_bytes / (1024 * 1024)) / self.transfer_time
        return 0.0

@dataclass
class RemoteFileInfo:
    """Information about a remote file."""
    path: str
    size: int
    mode: int
    modified_time: float
    is_directory: bool
    owner: str = ""
    group: str = ""
    
    @property
    def permissions(self) -> str:
        """Get file permissions as string (e.g., 'rwxr-xr-x')."""
        return stat.filemode(self.mode)
    
    @property
    def size_human(self) -> str:
        """Get human-readable file size."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if self.size < 1024.0:
                return f"{self.size:.1f} {unit}"
            self.size /= 1024.0
        return f"{self.size:.1f} PB"

class AdvancedSSHClient(BasicSSHClient):
    """
    Advanced SSH client with file operations, tunneling, and batch processing.
    Extends BasicSSHClient with additional functionality.
    """
    
    def __init__(self, config: ServerConfig):
        super().__init__(config)
        self.sftp_client: Optional[paramiko.SFTPClient] = None
        self.scp_client: Optional[scp.SCPClient] = None
    
    def connect(self) -> None:
        """Establish SSH connection and initialize SFTP/SCP clients."""
        super().connect()
        
        try:
            # Initialize SFTP client
            self.sftp_client = self.client.open_sftp()
            logger.info("SFTP client initialized")
            
            # Initialize SCP client
            self.scp_client = scp.SCPClient(self.client.get_transport())
            logger.info("SCP client initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize file transfer clients: {e}")
            raise
    
    def disconnect(self) -> None:
        """Close all connections."""
        if self.scp_client:
            self.scp_client.close()
        if self.sftp_client:
            self.sftp_client.close()
        super().disconnect()
    
    def upload_file(self, local_path: str, remote_path: str, 
                   preserve_times: bool = True) -> FileTransferResult:
        """
        Upload a file to the remote server.
        
        Args:
            local_path: Local file path
            remote_path: Remote file path
            preserve_times: Whether to preserve file timestamps
            
        Returns:
            FileTransferResult object
        """
        local_file = Path(local_path)
        if not local_file.exists():
            return FileTransferResult(
                local_path=local_path,
                remote_path=remote_path,
                size_bytes=0,
                transfer_time=0,
                success=False,
                error_message="Local file not found"
            )
        
        try:
            start_time = time.time()
            file_size = local_file.stat().st_size
            
            # Upload using SCP for better performance
            self.scp_client.put(local_path, remote_path, preserve_times=preserve_times)
            
            transfer_time = time.time() - start_time
            
            logger.info(f"Uploaded {local_path} to {remote_path} "
                       f"({file_size} bytes in {transfer_time:.2f}s)")
            
            return FileTransferResult(
                local_path=local_path,
                remote_path=remote_path,
                size_bytes=file_size,
                transfer_time=transfer_time,
                success=True
            )
            
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            return FileTransferResult(
                local_path=local_path,
                remote_path=remote_path,
                size_bytes=0,
                transfer_time=0,
                success=False,
                error_message=str(e)
            )
    
    def download_file(self, remote_path: str, local_path: str,
                     preserve_times: bool = True) -> FileTransferResult:
        """
        Download a file from the remote server.
        
        Args:
            remote_path: Remote file path
            local_path: Local file path
            preserve_times: Whether to preserve file timestamps
            
        Returns:
            FileTransferResult object
        """
        try:
            # Get remote file info
            remote_stat = self.sftp_client.stat(remote_path)
            file_size = remote_stat.st_size
            
            start_time = time.time()
            
            # Download using SCP
            self.scp_client.get(remote_path, local_path, preserve_times=preserve_times)
            
            transfer_time = time.time() - start_time
            
            logger.info(f"Downloaded {remote_path} to {local_path} "
                       f"({file_size} bytes in {transfer_time:.2f}s)")
            
            return FileTransferResult(
                local_path=local_path,
                remote_path=remote_path,
                size_bytes=file_size,
                transfer_time=transfer_time,
                success=True
            )
            
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return FileTransferResult(
                local_path=local_path,
                remote_path=remote_path,
                size_bytes=0,
                transfer_time=0,
                success=False,
                error_message=str(e)
            )
    
    def list_directory(self, remote_path: str = ".") -> List[RemoteFileInfo]:
        """
        List directory contents with detailed file information.
        
        Args:
            remote_path: Remote directory path
            
        Returns:
            List of RemoteFileInfo objects
        """
        try:
            files = []
            
            # Get directory listing with attributes
            for file_attr in self.sftp_client.listdir_attr(remote_path):
                file_info = RemoteFileInfo(
                    path=os.path.join(remote_path, file_attr.filename),
                    size=file_attr.st_size or 0,
                    mode=file_attr.st_mode or 0,
                    modified_time=file_attr.st_mtime or 0,
                    is_directory=stat.S_ISDIR(file_attr.st_mode or 0)
                )
                files.append(file_info)
            
            return sorted(files, key=lambda f: (not f.is_directory, f.path))
            
        except Exception as e:
            logger.error(f"Failed to list directory {remote_path}: {e}")
            return []
    
    def find_files(self, remote_path: str, pattern: str = "*",
                  recursive: bool = True) -> Generator[RemoteFileInfo, None, None]:
        """
        Find files matching a pattern (generator for memory efficiency).
        
        Args:
            remote_path: Starting directory path
            pattern: File pattern (supports wildcards)
            recursive: Whether to search recursively
            
        Yields:
            RemoteFileInfo objects for matching files
        """
        try:
            for file_info in self.list_directory(remote_path):
                if not file_info.is_directory:
                    if fnmatch.fnmatch(os.path.basename(file_info.path), pattern):
                        yield file_info
                elif recursive and file_info.is_directory:
                    # Recursively search subdirectories
                    yield from self.find_files(file_info.path, pattern, recursive)
                    
        except Exception as e:
            logger.error(f"Error finding files in {remote_path}: {e}")
    
    def sync_directory(self, local_dir: str, remote_dir: str,
                      direction: str = "upload", delete_extra: bool = False) -> Dict[str, Any]:
        """
        Synchronize directories between local and remote.
        
        Args:
            local_dir: Local directory path
            remote_dir: Remote directory path
            direction: "upload", "download", or "bidirectional"
            delete_extra: Whether to delete files not present in source
            
        Returns:
            Dictionary with sync statistics
        """
        stats = {
            'files_transferred': 0,
            'bytes_transferred': 0,
            'files_deleted': 0,
            'errors': []
        }
        
        try:
            if direction in ["upload", "bidirectional"]:
                stats.update(self._sync_upload(local_dir, remote_dir, delete_extra))
            
            if direction in ["download", "bidirectional"]:
                download_stats = self._sync_download(remote_dir, local_dir, delete_extra)
                stats['files_transferred'] += download_stats['files_transferred']
                stats['bytes_transferred'] += download_stats['bytes_transferred']
                stats['files_deleted'] += download_stats['files_deleted']
                stats['errors'].extend(download_stats['errors'])
            
        except Exception as e:
            stats['errors'].append(str(e))
            logger.error(f"Directory sync failed: {e}")
        
        return stats
    
    def _sync_upload(self, local_dir: str, remote_dir: str, delete_extra: bool) -> Dict[str, Any]:
        """Helper method for upload synchronization."""
        stats = {'files_transferred': 0, 'bytes_transferred': 0, 'files_deleted': 0, 'errors': []}
        
        # Ensure remote directory exists
        try:
            self.sftp_client.mkdir(remote_dir)
        except:
            pass  # Directory might already exist
        
        # Upload local files
        for local_file in Path(local_dir).rglob('*'):
            if local_file.is_file():
                relative_path = local_file.relative_to(local_dir)
                remote_file = os.path.join(remote_dir, str(relative_path)).replace('\\', '/')
                
                # Create remote directory if needed
                remote_file_dir = os.path.dirname(remote_file)
                try:
                    self.sftp_client.mkdir(remote_file_dir)
                except:
                    pass
                
                # Upload file
                result = self.upload_file(str(local_file), remote_file)
                if result.success:
                    stats['files_transferred'] += 1
                    stats['bytes_transferred'] += result.size_bytes
                else:
                    stats['errors'].append(f"Upload failed: {result.error_message}")
        
        return stats
    
    def _sync_download(self, remote_dir: str, local_dir: str, delete_extra: bool) -> Dict[str, Any]:
        """Helper method for download synchronization."""
        stats = {'files_transferred': 0, 'bytes_transferred': 0, 'files_deleted': 0, 'errors': []}
        
        # Create local directory
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        
        # Download remote files
        for file_info in self.find_files(remote_dir, recursive=True):
            if not file_info.is_directory:
                relative_path = os.path.relpath(file_info.path, remote_dir)
                local_file = os.path.join(local_dir, relative_path)
                
                # Create local directory if needed
                Path(local_file).parent.mkdir(parents=True, exist_ok=True)
                
                # Download file
                result = self.download_file(file_info.path, local_file)
                if result.success:
                    stats['files_transferred'] += 1
                    stats['bytes_transferred'] += result.size_bytes
                else:
                    stats['errors'].append(f"Download failed: {result.error_message}")
        
        return stats
    
    def execute_script(self, script_content: str, script_name: str = "temp_script.sh",
                      cleanup: bool = True) -> CommandResult:
        """
        Upload and execute a script on the remote server.
        
        Args:
            script_content: Script content as string
            script_name: Name for the temporary script file
            cleanup: Whether to delete the script after execution
            
        Returns:
            CommandResult object
        """
        remote_script_path = f"/tmp/{script_name}"
        
        try:
            # Upload script content
            with self.sftp_client.open(remote_script_path, 'w') as remote_file:
                remote_file.write(script_content)
            
            # Make script executable
            self.sftp_client.chmod(remote_script_path, 0o755)
            
            # Execute script
            result = self.execute_command(f"bash {remote_script_path}")
            
            # Cleanup if requested
            if cleanup:
                try:
                    self.sftp_client.remove(remote_script_path)
                except:
                    pass  # Ignore cleanup errors
            
            return result
            
        except Exception as e:
            logger.error(f"Script execution failed: {e}")
            raise CommandExecutionError(f"Script execution failed: {e}")
    
    def create_tunnel(self, local_port: int, remote_host: str, remote_port: int) -> threading.Thread:
        """
        Create an SSH tunnel (port forwarding).
        
        Args:
            local_port: Local port to bind
            remote_host: Remote host to connect to
            remote_port: Remote port to connect to
            
        Returns:
            Thread object running the tunnel
        """
        def tunnel_worker():
            try:
                transport = self.client.get_transport()
                while self.connected:
                    try:
                        # This is a simplified tunnel implementation
                        # In production, you'd want a more robust implementation
                        channel = transport.open_channel(
                            'direct-tcpip',
                            (remote_host, remote_port),
                            ('localhost', local_port)
                        )
                        # Handle channel data transfer here
                        time.sleep(1)
                    except Exception as e:
                        logger.error(f"Tunnel error: {e}")
                        break
            except Exception as e:
                logger.error(f"Tunnel worker failed: {e}")
        
        tunnel_thread = threading.Thread(target=tunnel_worker, daemon=True)
        tunnel_thread.start()
        logger.info(f"SSH tunnel created: localhost:{local_port} -> {remote_host}:{remote_port}")
        
        return tunnel_thread

# Batch operations and parallel processing
class BatchSSHOperations:
    """
    Batch operations for managing multiple SSH connections and parallel execution.
    Demonstrates threading, connection pooling, and parallel processing concepts.
    """
    
    def __init__(self, max_concurrent_connections: int = 5):
        self.max_concurrent_connections = max_concurrent_connections
        self.connection_pool: queue.Queue = queue.Queue()
        self.results_queue: queue.Queue = queue.Queue()
    
    def execute_on_multiple_servers(self, servers: List[ServerConfig], 
                                   command: str) -> Dict[str, CommandResult]:
        """
        Execute a command on multiple servers in parallel.
        
        Args:
            servers: List of ServerConfig objects
            command: Command to execute
            
        Returns:
            Dictionary mapping hostname to CommandResult
        """
        results = {}
        
        def worker(server_config: ServerConfig) -> None:
            try:
                with AdvancedSSHClient(server_config) as ssh:
                    result = ssh.execute_command(command)
                    results[server_config.hostname] = result
            except Exception as e:
                results[server_config.hostname] = CommandResult(
                    command=command,
                    exit_code=-1,
                    stdout="",
                    stderr=str(e),
                    execution_time=0,
                    success=False
                )
        
        # Execute in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=self.max_concurrent_connections) as executor:
            futures = [executor.submit(worker, server) for server in servers]
            
            # Wait for all tasks to complete
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Batch operation failed: {e}")
        
        return results
    
    def distribute_files(self, servers: List[ServerConfig], 
                        local_files: List[str], remote_dir: str) -> Dict[str, List[FileTransferResult]]:
        """
        Distribute files to multiple servers in parallel.
        
        Args:
            servers: List of ServerConfig objects
            local_files: List of local file paths
            remote_dir: Remote directory path
            
        Returns:
            Dictionary mapping hostname to list of FileTransferResults
        """
        results = {}
        
        def worker(server_config: ServerConfig) -> None:
            server_results = []
            try:
                with AdvancedSSHClient(server_config) as ssh:
                    for local_file in local_files:
                        remote_file = os.path.join(remote_dir, os.path.basename(local_file))
                        result = ssh.upload_file(local_file, remote_file)
                        server_results.append(result)
                
                results[server_config.hostname] = server_results
                
            except Exception as e:
                logger.error(f"File distribution to {server_config.hostname} failed: {e}")
                results[server_config.hostname] = []
        
        with ThreadPoolExecutor(max_workers=self.max_concurrent_connections) as executor:
            futures = [executor.submit(worker, server) for server in servers]
            
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Batch file distribution failed: {e}")
        
        return results

# Utility functions for advanced operations
def compress_and_upload(ssh_client: AdvancedSSHClient, local_dir: str, 
                       remote_path: str) -> FileTransferResult:
    """
    Compress a local directory and upload as a single archive.
    
    Args:
        ssh_client: AdvancedSSHClient instance
        local_dir: Local directory to compress
        remote_path: Remote path for the archive
        
    Returns:
        FileTransferResult object
    """
    archive_name = f"{os.path.basename(local_dir)}.tar.gz"
    local_archive = f"/tmp/{archive_name}"
    
    try:
        # Create compressed archive
        with tarfile.open(local_archive, "w:gz") as tar:
            tar.add(local_dir, arcname=os.path.basename(local_dir))
        
        # Upload archive
        result = ssh_client.upload_file(local_archive, remote_path)
        
        # Cleanup local archive
        os.remove(local_archive)
        
        return result
        
    except Exception as e:
        logger.error(f"Compress and upload failed: {e}")
        return FileTransferResult(
            local_path=local_dir,
            remote_path=remote_path,
            size_bytes=0,
            transfer_time=0,
            success=False,
            error_message=str(e)
        )

def download_and_extract(ssh_client: AdvancedSSHClient, remote_archive: str,
                        local_dir: str) -> bool:
    """
    Download and extract a remote archive.
    
    Args:
        ssh_client: AdvancedSSHClient instance
        remote_archive: Remote archive path
        local_dir: Local directory to extract to
        
    Returns:
        True if successful, False otherwise
    """
    local_archive = f"/tmp/{os.path.basename(remote_archive)}"
    
    try:
        # Download archive
        result = ssh_client.download_file(remote_archive, local_archive)
        if not result.success:
            return False
        
        # Extract archive
        with tarfile.open(local_archive, "r:gz") as tar:
            tar.extractall(local_dir)
        
        # Cleanup local archive
        os.remove(local_archive)
        
        return True
        
    except Exception as e:
        logger.error(f"Download and extract failed: {e}")
        return False

# Demonstration functions
def demonstrate_file_operations():
    """Demonstrate advanced file operations."""
    print("=== Advanced File Operations Demo ===")
    
    # This would require actual server credentials
    config = ServerConfig(
        hostname="example.com",
        username="user",
        password="password"
    )
    
    try:
        with AdvancedSSHClient(config) as ssh:
            # List directory contents
            files = ssh.list_directory("/home/user")
            print(f"Found {len(files)} files:")
            for file_info in files[:5]:  # Show first 5
                print(f"  {file_info.permissions} {file_info.size_human} {file_info.path}")
            
            # Find specific files
            python_files = list(ssh.find_files("/home/user", "*.py"))
            print(f"Found {len(python_files)} Python files")
            
            # Upload a file
            # result = ssh.upload_file("local_file.txt", "/remote/path/file.txt")
            # print(f"Upload result: {result.success}")
            
    except Exception as e:
        logger.error(f"File operations demo failed: {e}")

def demonstrate_batch_operations():
    """Demonstrate batch operations on multiple servers."""
    print("=== Batch Operations Demo ===")
    
    # Example server configurations
    servers = [
        ServerConfig(hostname="server1.example.com", username="user", password="pass1"),
        ServerConfig(hostname="server2.example.com", username="user", password="pass2"),
        ServerConfig(hostname="server3.example.com", username="user", password="pass3"),
    ]
    
    batch_ops = BatchSSHOperations(max_concurrent_connections=3)
    
    # Execute command on all servers
    results = batch_ops.execute_on_multiple_servers(servers, "uptime")
    
    for hostname, result in results.items():
        print(f"{hostname}: {result.stdout if result.success else result.stderr}")

def main():
    """Main demonstration function."""
    print("=== Advanced SSH Operations Examples ===")
    
    print("\n1. File Operations Demo")
    print("   (Requires server credentials - modify demonstrate_file_operations())")
    # demonstrate_file_operations()
    
    print("\n2. Batch Operations Demo")
    print("   (Requires multiple server credentials - modify demonstrate_batch_operations())")
    # demonstrate_batch_operations()
    
    print("\nTo use these examples:")
    print("1. Install dependencies: pip install paramiko scp")
    print("2. Modify server credentials in the demo functions")
    print("3. Run the appropriate demonstration function")
    
    print("\nAdvanced features demonstrated:")
    print("- File upload/download with progress tracking")
    print("- Directory synchronization")
    print("- Remote script execution")
    print("- SSH tunneling")
    print("- Batch operations on multiple servers")
    print("- Parallel file distribution")
    print("- Archive compression and extraction")

if __name__ == "__main__":
    main()
