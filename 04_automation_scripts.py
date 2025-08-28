"""
Unix Server Automation Scripts
This module demonstrates automation scripts for common server administration tasks.
Showcases Python concepts like task automation, configuration management,
deployment automation, and infrastructure as code principles.
"""

import os
import json
import yaml
import time
import shutil
import tempfile
import subprocess
from typing import Dict, List, Optional, Any, Callable, Union
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import queue
import hashlib
import tarfile
import gzip

from advanced_ssh_operations import AdvancedSSHClient, ServerConfig, BatchSSHOperations, logger

@dataclass
class AutomationTask:
    """Represents an automation task."""
    name: str
    description: str
    commands: List[str]
    prerequisites: List[str] = field(default_factory=list)
    rollback_commands: List[str] = field(default_factory=list)
    timeout_seconds: int = 300
    retry_count: int = 3
    critical: bool = False
    
    def __post_init__(self):
        """Validate task configuration."""
        if not self.commands:
            raise ValueError("Task must have at least one command")

@dataclass
class TaskResult:
    """Result of an automation task execution."""
    task_name: str
    success: bool
    output: str
    error: str
    execution_time: float
    retry_attempts: int
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class DeploymentConfig:
    """Configuration for application deployment."""
    app_name: str
    version: str
    source_path: str
    target_path: str
    service_name: str
    backup_enabled: bool = True
    health_check_url: Optional[str] = None
    environment_vars: Dict[str, str] = field(default_factory=dict)
    pre_deploy_tasks: List[str] = field(default_factory=list)
    post_deploy_tasks: List[str] = field(default_factory=list)

class TaskExecutor:
    """
    Execute automation tasks with error handling and rollback capabilities.
    Demonstrates task orchestration and error recovery patterns.
    """
    
    def __init__(self, ssh_client: AdvancedSSHClient):
        self.ssh_client = ssh_client
        self.task_history: List[TaskResult] = []
        self.rollback_stack: List[AutomationTask] = []
    
    def execute_task(self, task: AutomationTask) -> TaskResult:
        """
        Execute a single automation task with retry logic.
        
        Args:
            task: AutomationTask to execute
            
        Returns:
            TaskResult with execution details
        """
        start_time = time.time()
        retry_attempts = 0
        last_error = ""
        
        logger.info(f"Executing task: {task.name}")
        
        # Check prerequisites
        if not self._check_prerequisites(task.prerequisites):
            return TaskResult(
                task_name=task.name,
                success=False,
                output="",
                error="Prerequisites not met",
                execution_time=time.time() - start_time,
                retry_attempts=0
            )
        
        # Execute with retries
        while retry_attempts <= task.retry_count:
            try:
                output_lines = []
                error_lines = []
                
                # Execute each command in the task
                for command in task.commands:
                    logger.debug(f"Executing command: {command}")
                    
                    result = self.ssh_client.execute_command(command, timeout=task.timeout_seconds)
                    
                    if result.success:
                        output_lines.append(f"Command: {command}")
                        output_lines.append(f"Output: {result.stdout}")
                    else:
                        error_lines.append(f"Command: {command}")
                        error_lines.append(f"Error: {result.stderr}")
                        
                        if task.critical:
                            # Critical task failure - stop execution
                            raise Exception(f"Critical command failed: {command}")
                
                # If we get here, all commands succeeded
                execution_time = time.time() - start_time
                
                task_result = TaskResult(
                    task_name=task.name,
                    success=True,
                    output="\n".join(output_lines),
                    error="\n".join(error_lines) if error_lines else "",
                    execution_time=execution_time,
                    retry_attempts=retry_attempts
                )
                
                # Add to rollback stack if rollback commands exist
                if task.rollback_commands:
                    self.rollback_stack.append(task)
                
                self.task_history.append(task_result)
                logger.info(f"Task '{task.name}' completed successfully")
                
                return task_result
                
            except Exception as e:
                retry_attempts += 1
                last_error = str(e)
                
                if retry_attempts <= task.retry_count:
                    logger.warning(f"Task '{task.name}' failed (attempt {retry_attempts}), retrying: {e}")
                    time.sleep(2 ** retry_attempts)  # Exponential backoff
                else:
                    logger.error(f"Task '{task.name}' failed after {retry_attempts} attempts: {e}")
        
        # Task failed after all retries
        execution_time = time.time() - start_time
        
        task_result = TaskResult(
            task_name=task.name,
            success=False,
            output="",
            error=last_error,
            execution_time=execution_time,
            retry_attempts=retry_attempts
        )
        
        self.task_history.append(task_result)
        return task_result
    
    def execute_task_sequence(self, tasks: List[AutomationTask], 
                            stop_on_failure: bool = True) -> List[TaskResult]:
        """
        Execute a sequence of tasks.
        
        Args:
            tasks: List of AutomationTask objects
            stop_on_failure: Whether to stop on first failure
            
        Returns:
            List of TaskResult objects
        """
        results = []
        
        for task in tasks:
            result = self.execute_task(task)
            results.append(result)
            
            if not result.success and stop_on_failure:
                logger.error(f"Stopping task sequence due to failure in '{task.name}'")
                break
        
        return results
    
    def rollback_tasks(self, count: Optional[int] = None) -> List[TaskResult]:
        """
        Rollback previously executed tasks.
        
        Args:
            count: Number of tasks to rollback (None for all)
            
        Returns:
            List of rollback TaskResult objects
        """
        if not self.rollback_stack:
            logger.info("No tasks to rollback")
            return []
        
        rollback_count = count or len(self.rollback_stack)
        rollback_results = []
        
        logger.info(f"Rolling back {rollback_count} tasks")
        
        # Rollback in reverse order
        for _ in range(min(rollback_count, len(self.rollback_stack))):
            task = self.rollback_stack.pop()
            
            # Create rollback task
            rollback_task = AutomationTask(
                name=f"Rollback: {task.name}",
                description=f"Rollback for {task.description}",
                commands=task.rollback_commands,
                timeout_seconds=task.timeout_seconds
            )
            
            result = self.execute_task(rollback_task)
            rollback_results.append(result)
        
        return rollback_results
    
    def _check_prerequisites(self, prerequisites: List[str]) -> bool:
        """Check if prerequisites are met."""
        if not prerequisites:
            return True
        
        for prerequisite in prerequisites:
            result = self.ssh_client.execute_command(prerequisite)
            if not result.success:
                logger.error(f"Prerequisite check failed: {prerequisite}")
                return False
        
        return True

class ServerProvisioner:
    """
    Automated server provisioning and configuration.
    Demonstrates infrastructure automation and configuration management.
    """
    
    def __init__(self, ssh_client: AdvancedSSHClient):
        self.ssh_client = ssh_client
        self.task_executor = TaskExecutor(ssh_client)
        
        # Predefined provisioning tasks
        self.provisioning_tasks = {
            'update_system': self._create_system_update_task(),
            'install_docker': self._create_docker_installation_task(),
            'setup_firewall': self._create_firewall_setup_task(),
            'configure_ssh': self._create_ssh_hardening_task(),
            'install_monitoring': self._create_monitoring_setup_task(),
            'setup_backup': self._create_backup_setup_task()
        }
    
    def provision_server(self, tasks: List[str], config: Dict[str, Any] = None) -> Dict[str, TaskResult]:
        """
        Provision a server with specified tasks.
        
        Args:
            tasks: List of task names to execute
            config: Configuration parameters
            
        Returns:
            Dictionary mapping task names to results
        """
        config = config or {}
        results = {}
        
        logger.info(f"Starting server provisioning with tasks: {tasks}")
        
        for task_name in tasks:
            if task_name not in self.provisioning_tasks:
                logger.error(f"Unknown provisioning task: {task_name}")
                continue
            
            task = self.provisioning_tasks[task_name]
            
            # Apply configuration to task commands
            if config:
                task = self._apply_config_to_task(task, config)
            
            result = self.task_executor.execute_task(task)
            results[task_name] = result
            
            if not result.success:
                logger.error(f"Provisioning task '{task_name}' failed")
                break
        
        logger.info("Server provisioning completed")
        return results
    
    def _create_system_update_task(self) -> AutomationTask:
        """Create system update task."""
        return AutomationTask(
            name="System Update",
            description="Update system packages",
            commands=[
                "sudo apt-get update",
                "sudo apt-get upgrade -y",
                "sudo apt-get autoremove -y",
                "sudo apt-get autoclean"
            ],
            prerequisites=["which apt-get"],
            timeout_seconds=600
        )
    
    def _create_docker_installation_task(self) -> AutomationTask:
        """Create Docker installation task."""
        return AutomationTask(
            name="Docker Installation",
            description="Install Docker and Docker Compose",
            commands=[
                "curl -fsSL https://get.docker.com -o get-docker.sh",
                "sudo sh get-docker.sh",
                "sudo usermod -aG docker $USER",
                "sudo systemctl enable docker",
                "sudo systemctl start docker",
                "sudo curl -L \"https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)\" -o /usr/local/bin/docker-compose",
                "sudo chmod +x /usr/local/bin/docker-compose",
                "docker --version",
                "docker-compose --version"
            ],
            prerequisites=["which curl"],
            rollback_commands=[
                "sudo systemctl stop docker",
                "sudo systemctl disable docker",
                "sudo apt-get remove -y docker-ce docker-ce-cli containerd.io",
                "sudo rm -f /usr/local/bin/docker-compose",
                "sudo rm -f get-docker.sh"
            ],
            timeout_seconds=600
        )
    
    def _create_firewall_setup_task(self) -> AutomationTask:
        """Create firewall setup task."""
        return AutomationTask(
            name="Firewall Setup",
            description="Configure UFW firewall",
            commands=[
                "sudo ufw --force reset",
                "sudo ufw default deny incoming",
                "sudo ufw default allow outgoing",
                "sudo ufw allow ssh",
                "sudo ufw allow 80/tcp",
                "sudo ufw allow 443/tcp",
                "sudo ufw --force enable",
                "sudo ufw status verbose"
            ],
            prerequisites=["which ufw"],
            rollback_commands=[
                "sudo ufw --force disable",
                "sudo ufw --force reset"
            ]
        )
    
    def _create_ssh_hardening_task(self) -> AutomationTask:
        """Create SSH hardening task."""
        return AutomationTask(
            name="SSH Hardening",
            description="Harden SSH configuration",
            commands=[
                "sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup",
                "sudo sed -i 's/#PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config",
                "sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config",
                "sudo sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config",
                "sudo sed -i 's/#Port 22/Port 2222/' /etc/ssh/sshd_config",
                "sudo systemctl restart sshd",
                "sudo systemctl status sshd"
            ],
            rollback_commands=[
                "sudo cp /etc/ssh/sshd_config.backup /etc/ssh/sshd_config",
                "sudo systemctl restart sshd"
            ]
        )
    
    def _create_monitoring_setup_task(self) -> AutomationTask:
        """Create monitoring setup task."""
        return AutomationTask(
            name="Monitoring Setup",
            description="Install and configure monitoring tools",
            commands=[
                "sudo apt-get install -y htop iotop nethogs",
                "sudo apt-get install -y sysstat",
                "sudo systemctl enable sysstat",
                "sudo systemctl start sysstat",
                "which htop && which iotop && which nethogs"
            ],
            rollback_commands=[
                "sudo systemctl stop sysstat",
                "sudo systemctl disable sysstat",
                "sudo apt-get remove -y htop iotop nethogs sysstat"
            ]
        )
    
    def _create_backup_setup_task(self) -> AutomationTask:
        """Create backup setup task."""
        return AutomationTask(
            name="Backup Setup",
            description="Setup automated backup system",
            commands=[
                "sudo mkdir -p /backup/scripts",
                "sudo mkdir -p /backup/data",
                "sudo chown $USER:$USER /backup",
                "echo '#!/bin/bash' | sudo tee /backup/scripts/daily_backup.sh",
                "echo 'tar -czf /backup/data/backup_$(date +%Y%m%d).tar.gz /home /etc' | sudo tee -a /backup/scripts/daily_backup.sh",
                "sudo chmod +x /backup/scripts/daily_backup.sh",
                "echo '0 2 * * * /backup/scripts/daily_backup.sh' | crontab -",
                "crontab -l"
            ],
            rollback_commands=[
                "crontab -r",
                "sudo rm -rf /backup"
            ]
        )
    
    def _apply_config_to_task(self, task: AutomationTask, config: Dict[str, Any]) -> AutomationTask:
        """Apply configuration parameters to task commands."""
        # Create a copy of the task with modified commands
        modified_commands = []
        
        for command in task.commands:
            # Replace configuration placeholders
            for key, value in config.items():
                placeholder = f"{{{key}}}"
                command = command.replace(placeholder, str(value))
            
            modified_commands.append(command)
        
        return AutomationTask(
            name=task.name,
            description=task.description,
            commands=modified_commands,
            prerequisites=task.prerequisites,
            rollback_commands=task.rollback_commands,
            timeout_seconds=task.timeout_seconds,
            retry_count=task.retry_count,
            critical=task.critical
        )

class ApplicationDeployer:
    """
    Automated application deployment system.
    Demonstrates deployment automation, blue-green deployments, and rollback strategies.
    """
    
    def __init__(self, ssh_client: AdvancedSSHClient):
        self.ssh_client = ssh_client
        self.task_executor = TaskExecutor(ssh_client)
    
    def deploy_application(self, config: DeploymentConfig) -> Dict[str, Any]:
        """
        Deploy an application using the specified configuration.
        
        Args:
            config: DeploymentConfig object
            
        Returns:
            Dictionary with deployment results
        """
        deployment_id = f"{config.app_name}_{config.version}_{int(time.time())}"
        
        logger.info(f"Starting deployment: {deployment_id}")
        
        deployment_result = {
            'deployment_id': deployment_id,
            'app_name': config.app_name,
            'version': config.version,
            'start_time': datetime.now(),
            'success': False,
            'tasks': {},
            'rollback_performed': False
        }
        
        try:
            # Pre-deployment tasks
            if config.pre_deploy_tasks:
                logger.info("Executing pre-deployment tasks")
                pre_deploy_results = self._execute_deployment_tasks(
                    config.pre_deploy_tasks, "pre-deploy"
                )
                deployment_result['tasks']['pre_deploy'] = pre_deploy_results
                
                if not all(r.success for r in pre_deploy_results):
                    raise Exception("Pre-deployment tasks failed")
            
            # Create backup if enabled
            if config.backup_enabled:
                logger.info("Creating backup")
                backup_result = self._create_backup(config)
                deployment_result['tasks']['backup'] = backup_result
                
                if not backup_result.success:
                    raise Exception("Backup creation failed")
            
            # Stop application service
            logger.info("Stopping application service")
            stop_result = self._stop_service(config.service_name)
            deployment_result['tasks']['stop_service'] = stop_result
            
            # Deploy new version
            logger.info("Deploying new version")
            deploy_result = self._deploy_files(config)
            deployment_result['tasks']['deploy_files'] = deploy_result
            
            if not deploy_result.success:
                raise Exception("File deployment failed")
            
            # Update configuration
            logger.info("Updating configuration")
            config_result = self._update_configuration(config)
            deployment_result['tasks']['update_config'] = config_result
            
            # Start application service
            logger.info("Starting application service")
            start_result = self._start_service(config.service_name)
            deployment_result['tasks']['start_service'] = start_result
            
            if not start_result.success:
                raise Exception("Service start failed")
            
            # Health check
            if config.health_check_url:
                logger.info("Performing health check")
                health_result = self._perform_health_check(config.health_check_url)
                deployment_result['tasks']['health_check'] = health_result
                
                if not health_result.success:
                    raise Exception("Health check failed")
            
            # Post-deployment tasks
            if config.post_deploy_tasks:
                logger.info("Executing post-deployment tasks")
                post_deploy_results = self._execute_deployment_tasks(
                    config.post_deploy_tasks, "post-deploy"
                )
                deployment_result['tasks']['post_deploy'] = post_deploy_results
            
            deployment_result['success'] = True
            deployment_result['end_time'] = datetime.now()
            
            logger.info(f"Deployment {deployment_id} completed successfully")
            
        except Exception as e:
            logger.error(f"Deployment {deployment_id} failed: {e}")
            deployment_result['error'] = str(e)
            deployment_result['end_time'] = datetime.now()
            
            # Perform rollback
            logger.info("Performing rollback")
            rollback_result = self._rollback_deployment(config, deployment_result)
            deployment_result['rollback_performed'] = True
            deployment_result['rollback_result'] = rollback_result
        
        return deployment_result
    
    def _execute_deployment_tasks(self, task_commands: List[str], 
                                 task_type: str) -> List[TaskResult]:
        """Execute deployment-related tasks."""
        results = []
        
        for i, command in enumerate(task_commands):
            task = AutomationTask(
                name=f"{task_type}_task_{i+1}",
                description=f"{task_type} task: {command}",
                commands=[command]
            )
            
            result = self.task_executor.execute_task(task)
            results.append(result)
            
            if not result.success:
                break
        
        return results
    
    def _create_backup(self, config: DeploymentConfig) -> TaskResult:
        """Create backup of current application."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"/backup/{config.app_name}_{timestamp}"
        
        backup_task = AutomationTask(
            name="Create Backup",
            description="Backup current application",
            commands=[
                f"sudo mkdir -p {backup_path}",
                f"sudo cp -r {config.target_path} {backup_path}/",
                f"sudo tar -czf {backup_path}.tar.gz -C {backup_path} .",
                f"sudo rm -rf {backup_path}",
                f"ls -la {backup_path}.tar.gz"
            ]
        )
        
        return self.task_executor.execute_task(backup_task)
    
    def _stop_service(self, service_name: str) -> TaskResult:
        """Stop application service."""
        stop_task = AutomationTask(
            name="Stop Service",
            description=f"Stop {service_name} service",
            commands=[
                f"sudo systemctl stop {service_name}",
                f"sudo systemctl status {service_name} || true"
            ]
        )
        
        return self.task_executor.execute_task(stop_task)
    
    def _start_service(self, service_name: str) -> TaskResult:
        """Start application service."""
        start_task = AutomationTask(
            name="Start Service",
            description=f"Start {service_name} service",
            commands=[
                f"sudo systemctl start {service_name}",
                f"sudo systemctl enable {service_name}",
                f"sudo systemctl status {service_name}"
            ]
        )
        
        return self.task_executor.execute_task(start_task)
    
    def _deploy_files(self, config: DeploymentConfig) -> TaskResult:
        """Deploy application files."""
        # First, upload files to temporary location
        temp_path = f"/tmp/{config.app_name}_deploy_{int(time.time())}"
        
        try:
            # Upload source files
            upload_result = self.ssh_client.upload_file(
                config.source_path, 
                f"{temp_path}.tar.gz"
            )
            
            if not upload_result.success:
                return TaskResult(
                    task_name="Deploy Files",
                    success=False,
                    output="",
                    error="File upload failed",
                    execution_time=0,
                    retry_attempts=0
                )
            
            # Extract and deploy
            deploy_task = AutomationTask(
                name="Deploy Files",
                description="Extract and deploy application files",
                commands=[
                    f"sudo mkdir -p {temp_path}",
                    f"sudo tar -xzf {temp_path}.tar.gz -C {temp_path}",
                    f"sudo mkdir -p {config.target_path}",
                    f"sudo cp -r {temp_path}/* {config.target_path}/",
                    f"sudo chown -R www-data:www-data {config.target_path}",
                    f"sudo chmod -R 755 {config.target_path}",
                    f"sudo rm -rf {temp_path}*"
                ]
            )
            
            return self.task_executor.execute_task(deploy_task)
            
        except Exception as e:
            return TaskResult(
                task_name="Deploy Files",
                success=False,
                output="",
                error=str(e),
                execution_time=0,
                retry_attempts=0
            )
    
    def _update_configuration(self, config: DeploymentConfig) -> TaskResult:
        """Update application configuration."""
        config_commands = []
        
        # Set environment variables
        for key, value in config.environment_vars.items():
            config_commands.append(f"echo 'export {key}={value}' | sudo tee -a /etc/environment")
        
        # Reload environment
        config_commands.append("sudo systemctl daemon-reload")
        
        if config_commands:
            config_task = AutomationTask(
                name="Update Configuration",
                description="Update application configuration",
                commands=config_commands
            )
            
            return self.task_executor.execute_task(config_task)
        else:
            return TaskResult(
                task_name="Update Configuration",
                success=True,
                output="No configuration updates needed",
                error="",
                execution_time=0,
                retry_attempts=0
            )
    
    def _perform_health_check(self, health_check_url: str) -> TaskResult:
        """Perform application health check."""
        health_task = AutomationTask(
            name="Health Check",
            description="Check application health",
            commands=[
                f"curl -f {health_check_url} || exit 1",
                "sleep 5",
                f"curl -f {health_check_url} || exit 1"
            ],
            timeout_seconds=60
        )
        
        return self.task_executor.execute_task(health_task)
    
    def _rollback_deployment(self, config: DeploymentConfig, 
                           deployment_result: Dict[str, Any]) -> Dict[str, Any]:
        """Rollback failed deployment."""
        rollback_result = {
            'success': False,
            'tasks': {}
        }
        
        try:
            # Stop service
            stop_result = self._stop_service(config.service_name)
            rollback_result['tasks']['stop_service'] = stop_result
            
            # Restore from backup if backup was created
            if 'backup' in deployment_result['tasks'] and deployment_result['tasks']['backup'].success:
                restore_result = self._restore_from_backup(config)
                rollback_result['tasks']['restore_backup'] = restore_result
            
            # Start service
            start_result = self._start_service(config.service_name)
            rollback_result['tasks']['start_service'] = start_result
            
            rollback_result['success'] = start_result.success
            
        except Exception as e:
            rollback_result['error'] = str(e)
        
        return rollback_result
    
    def _restore_from_backup(self, config: DeploymentConfig) -> TaskResult:
        """Restore application from backup."""
        # Find latest backup
        find_backup_task = AutomationTask(
            name="Restore from Backup",
            description="Restore application from latest backup",
            commands=[
                f"LATEST_BACKUP=$(ls -t /backup/{config.app_name}_*.tar.gz | head -1)",
                f"sudo rm -rf {config.target_path}/*",
                f"sudo tar -xzf $LATEST_BACKUP -C {config.target_path}",
                f"sudo chown -R www-data:www-data {config.target_path}"
            ]
        )
        
        return self.task_executor.execute_task(find_backup_task)

class ConfigurationManager:
    """
    Configuration management system for maintaining server configurations.
    Demonstrates configuration as code and drift detection.
    """
    
    def __init__(self, ssh_client: AdvancedSSHClient):
        self.ssh_client = ssh_client
        self.task_executor = TaskExecutor(ssh_client)
    
    def apply_configuration(self, config_file: str) -> Dict[str, Any]:
        """
        Apply configuration from YAML file.
        
        Args:
            config_file: Path to YAML configuration file
            
        Returns:
            Dictionary with application results
        """
        try:
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)
            
            logger.info(f"Applying configuration from {config_file}")
            
            results = {
                'config_file': config_file,
                'success': True,
                'applied_configs': {},
                'errors': []
            }
            
            # Apply each configuration section
            for section_name, section_config in config.items():
                try:
                    section_result = self._apply_config_section(section_name, section_config)
                    results['applied_configs'][section_name] = section_result
                    
                    if not section_result['success']:
                        results['success'] = False
                        
                except Exception as e:
                    error_msg = f"Failed to apply {section_name}: {e}"
                    results['errors'].append(error_msg)
                    results['success'] = False
                    logger.error(error_msg)
            
            return results
            
        except Exception as e:
            logger.error(f"Configuration application failed: {e}")
            return {
                'config_file': config_file,
                'success': False,
                'error': str(e)
            }
    
    def _apply_config_section(self, section_name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a specific configuration section."""
        section_handlers = {
            'packages': self._apply_packages_config,
            'services': self._apply_services_config,
            'files': self._apply_files_config,
            'users': self._apply_users_config,
            'firewall': self._apply_firewall_config,
            'cron': self._apply_cron_config
        }
        
        if section_name in section_handlers:
            return section_handlers[section_name](config)
        else:
            return {
                'success': False,
                'error': f"Unknown configuration section: {section_name}"
            }
    
    def _apply_packages_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply package configuration."""
        results = {'success': True, 'installed': [], 'removed': [], 'errors': []}
        
        # Install packages
        if 'install' in config:
            for package in config['install']:
                task = AutomationTask(
                    name=f"Install {package}",
                    description=f"Install package {package}",
                    commands=[f"sudo apt-get install -y {package}"]
                )
                
                result = self.task_executor.execute_task(task)
                if result.success:
                    results['installed'].append(package)
                else:
                    results['errors'].append(f"Failed to install {package}: {result.error}")
                    results['success'] = False
        
        # Remove packages
        if 'remove' in config:
            for package in config['remove']:
                task = AutomationTask(
                    name=f"Remove {package}",
                    description=f"Remove package {package}",
                    commands=[f"sudo apt-get remove -y {package}"]
                )
                
                result = self.task_executor.execute_task(task)
                if result.success:
                    results['removed'].append(package)
                else:
                    results['errors'].append(f"Failed to remove {package}: {result.error}")
                    results['success'] = False
        
        return results
    
    def _apply_services_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply services configuration."""
        results = {'success': True, 'started': [], 'stopped': [], 'enabled': [], 'disabled': [], 'errors': []}
        
        for service_name, service_config in config.items():
            try:
                state = service_config.get('state', 'started')
                enabled = service_config.get('enabled', True)
                
                commands = []
                
                # Handle service state
                if state == 'started':
                    commands.append(f"sudo systemctl start {service_name}")
                    results['started'].append(service_name)
                elif state == 'stopped':
                    commands.append(f"sudo systemctl stop {service_name}")
                    results['stopped'].append(service_name)
                
                # Handle service enablement
                if enabled:
                    commands.append(f"sudo systemctl enable {service_name}")
                    results['enabled'].append(service_name)
                else:
                    commands.append(f"sudo systemctl disable {service_name}")
                    results['disabled'].append(service_name)
                
                # Execute commands
                if commands:
                    task = AutomationTask(
                        name=f"Configure {service_name}",
                        description=f"Configure service {service_name}",
                        commands=commands
                    )
                    
                    result = self.task_executor.execute_task(task)
                    if not result.success:
                        results['errors'].append(f"Failed to configure {service_name}: {result.error}")
                        results['success'] = False
                        
            except Exception as e:
                results['errors'].append(f"Service configuration error for {service_name}: {e}")
                results['success'] = False
        
        return results
    
    def _apply_files_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply files configuration."""
        results = {'success': True, 'created': [], 'errors': []}
        
        for file_path, file_config in config.items():
            try:
                content = file_config.get('content', '')
                owner = file_config.get('owner', 'root')
                group = file_config.get('group', 'root')
                mode = file_config.get('mode', '644')
                
                # Create temporary file with content
                temp_file = f"/tmp/config_file_{int(time.time())}"
                
                commands = [
                    f"echo '{content}' | sudo tee {temp_file}",
                    f"sudo mv {temp_file} {file_path}",
                    f"sudo chown {owner}:{group} {file_path}",
                    f"sudo chmod {mode} {file_path}"
                ]
                
                task = AutomationTask(
                    name=f"Create file {file_path}",
                    description=f"Create and configure file {file_path}",
                    commands=commands
                )
                
                result = self.task_executor.execute_task(task)
                if result.success:
                    results['created'].append(file_path)
                else:
                    results['errors'].append(f"Failed to create {file_path}: {result.error}")
                    results['success'] = False
                    
            except Exception as e:
                results['errors'].append(f"File configuration error for {file_path}: {e}")
                results['success'] = False
        
        return results
    
    def _apply_users_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply users configuration."""
        results = {'success': True, 'created': [], 'errors': []}
        
        for username, user_config in config.items():
            try:
                shell = user_config.get('shell', '/bin/bash')
                home = user_config.get('home', f'/home/{username}')
                groups = user_config.get('groups', [])
                
                commands = [
                    f"sudo useradd -m -d {home} -s {shell} {username} || true",
                ]
                
                # Add to groups
                for group in groups:
                    commands.append(f"sudo usermod -a -G {group} {username}")
                
                task = AutomationTask(
                    name=f"Create user {username}",
                    description=f"Create and configure user {username}",
                    commands=commands
                )
                
                result = self.task_executor.execute_task(task)
                if result.success:
                    results['created'].append(username)
                else:
                    results['errors'].append(f"Failed to create user {username}: {result.error}")
                    results['success'] = False
                    
            except Exception as e:
                results['errors'].append(f"User configuration error for {username}: {e}")
                results['success'] = False
        
        return results
    
    def _apply_firewall_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply firewall configuration."""
        results = {'success': True, 'rules_added': [], 'errors': []}
        
        try:
            # Reset firewall if requested
            if config.get('reset', False):
                reset_task = AutomationTask(
                    name="Reset Firewall",
                    description="Reset UFW firewall rules",
                    commands=["sudo ufw --force reset"]
                )
                self.task_executor.execute_task(reset_task)
            
            # Set default policies
            if 'default_incoming' in config:
                policy = config['default_incoming']
                task = AutomationTask(
                    name="Set Default Incoming Policy",
                    description=f"Set default incoming policy to {policy}",
                    commands=[f"sudo ufw default {policy} incoming"]
                )
                self.task_executor.execute_task(task)
            
            if 'default_outgoing' in config:
                policy = config['default_outgoing']
                task = AutomationTask(
                    name="Set Default Outgoing Policy",
                    description=f"Set default outgoing policy to {policy}",
                    commands=[f"sudo ufw default {policy} outgoing"]
                )
                self.task_executor.execute_task(task)
            
            # Add rules
            if 'rules' in config:
                for rule in config['rules']:
                    action = rule.get('action', 'allow')
                    port = rule.get('port')
                    protocol = rule.get('protocol', 'tcp')
                    source = rule.get('source', '')
                    
                    rule_cmd = f"sudo ufw {action}"
                    if source:
                        rule_cmd += f" from {source}"
                    if port:
                        rule_cmd += f" to any port {port}"
                    if protocol and port:
                        rule_cmd += f"/{protocol}"
                    
                    task = AutomationTask(
                        name=f"Add firewall rule",
                        description=f"Add firewall rule: {rule_cmd}",
                        commands=[rule_cmd]
                    )
                    
                    result = self.task_executor.execute_task(task)
                    if result.success:
                        results['rules_added'].append(rule)
                    else:
                        results['errors'].append(f"Failed to add rule {rule}: {result.error}")
                        results['success'] = False
            
            # Enable firewall
            if config.get('enabled', True):
                enable_task = AutomationTask(
                    name="Enable Firewall",
                    description="Enable UFW firewall",
                    commands=["sudo ufw --force enable"]
                )
                self.task_executor.execute_task(enable_task)
                
        except Exception as e:
            results['errors'].append(f"Firewall configuration error: {e}")
            results['success'] = False
        
        return results
    
    def _apply_cron_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply cron configuration."""
        results = {'success': True, 'jobs_added': [], 'errors': []}
        
        for job_name, job_config in config.items():
            try:
                schedule = job_config.get('schedule')
                command = job_config.get('command')
                user = job_config.get('user', 'root')
                
                if not schedule or not command:
                    results['errors'].append(f"Invalid cron job config for {job_name}")
                    results['success'] = False
                    continue
                
                cron_line = f"{schedule} {command}"
                
                task = AutomationTask(
                    name=f"Add cron job {job_name}",
                    description=f"Add cron job: {cron_line}",
                    commands=[
                        f"echo '{cron_line}' | sudo crontab -u {user} -",
                        f"sudo crontab -u {user} -l"
                    ]
                )
                
                result = self.task_executor.execute_task(task)
                if result.success:
                    results['jobs_added'].append(job_name)
                else:
                    results['errors'].append(f"Failed to add cron job {job_name}: {result.error}")
                    results['success'] = False
                    
            except Exception as e:
                results['errors'].append(f"Cron job configuration error for {job_name}: {e}")
                results['success'] = False
        
        return results
    
    def detect_configuration_drift(self, config_file: str) -> Dict[str, Any]:
        """
        Detect configuration drift by comparing current state with desired state.
        
        Args:
            config_file: Path to YAML configuration file
            
        Returns:
            Dictionary with drift detection results
        """
        try:
            with open(config_file, 'r') as f:
                desired_config = yaml.safe_load(f)
            
            drift_results = {
                'config_file': config_file,
                'drift_detected': False,
                'sections': {}
            }
            
            # Check each configuration section for drift
            for section_name, section_config in desired_config.items():
                section_drift = self._detect_section_drift(section_name, section_config)
                drift_results['sections'][section_name] = section_drift
                
                if section_drift.get('drift_detected', False):
                    drift_results['drift_detected'] = True
            
            return drift_results
            
        except Exception as e:
            logger.error(f"Configuration drift detection failed: {e}")
            return {
                'config_file': config_file,
                'error': str(e)
            }
    
    def _detect_section_drift(self, section_name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Detect drift in a specific configuration section."""
        drift_handlers = {
            'packages': self._detect_packages_drift,
            'services': self._detect_services_drift,
            'files': self._detect_files_drift
        }
        
        if section_name in drift_handlers:
            return drift_handlers[section_name](config)
        else:
            return {
                'drift_detected': False,
                'message': f"Drift detection not implemented for {section_name}"
            }
    
    def _detect_packages_drift(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Detect package configuration drift."""
        drift_result = {
            'drift_detected': False,
            'missing_packages': [],
            'unexpected_packages': []
        }
        
        # Check installed packages
        if 'install' in config:
            for package in config['install']:
                check_cmd = f"dpkg -l | grep '^ii' | grep {package}"
                result = self.ssh_client.execute_command(check_cmd)
                
                if not result.success:
                    drift_result['missing_packages'].append(package)
                    drift_result['drift_detected'] = True
        
        return drift_result
    
    def _detect_services_drift(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Detect services configuration drift."""
        drift_result = {
            'drift_detected': False,
            'service_issues': []
        }
        
        for service_name, service_config in config.items():
            expected_state = service_config.get('state', 'started')
            expected_enabled = service_config.get('enabled', True)
            
            # Check service state
            state_cmd = f"systemctl is-active {service_name}"
            state_result = self.ssh_client.execute_command(state_cmd)
            actual_state = state_result.stdout.strip() if state_result.success else 'unknown'
            
            # Check if service is enabled
            enabled_cmd = f"systemctl is-enabled {service_name}"
            enabled_result = self.ssh_client.execute_command(enabled_cmd)
            actual_enabled = enabled_result.stdout.strip() == 'enabled' if enabled_result.success else False
            
            # Detect drift
            state_drift = (expected_state == 'started' and actual_state != 'active') or \
                         (expected_state == 'stopped' and actual_state == 'active')
            
            enabled_drift = expected_enabled != actual_enabled
            
            if state_drift or enabled_drift:
                drift_result['service_issues'].append({
                    'service': service_name,
                    'expected_state': expected_state,
                    'actual_state': actual_state,
                    'expected_enabled': expected_enabled,
                    'actual_enabled': actual_enabled
                })
                drift_result['drift_detected'] = True
        
        return drift_result
    
    def _detect_files_drift(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Detect files configuration drift."""
        drift_result = {
            'drift_detected': False,
            'file_issues': []
        }
        
        for file_path, file_config in config.items():
            expected_content = file_config.get('content', '')
            expected_owner = file_config.get('owner', 'root')
            expected_group = file_config.get('group', 'root')
            expected_mode = file_config.get('mode', '644')
            
            # Check if file exists
            exists_cmd = f"test -f {file_path}"
            exists_result = self.ssh_client.execute_command(exists_cmd)
            
            if not exists_result.success:
                drift_result['file_issues'].append({
                    'file': file_path,
                    'issue': 'File does not exist'
                })
                drift_result['drift_detected'] = True
                continue
            
            # Check file content (simplified - just check if content matches)
            content_cmd = f"cat {file_path}"
            content_result = self.ssh_client.execute_command(content_cmd)
            
            if content_result.success and content_result.stdout.strip() != expected_content.strip():
                drift_result['file_issues'].append({
                    'file': file_path,
                    'issue': 'Content mismatch'
                })
                drift_result['drift_detected'] = True
            
            # Check file permissions
            stat_cmd = f"stat -c '%U:%G:%a' {file_path}"
            stat_result = self.ssh_client.execute_command(stat_cmd)
            
            if stat_result.success:
                actual_owner, actual_group, actual_mode = stat_result.stdout.strip().split(':')
                
                if (actual_owner != expected_owner or 
                    actual_group != expected_group or 
                    actual_mode != expected_mode):
                    
                    drift_result['file_issues'].append({
                        'file': file_path,
                        'issue': 'Permission mismatch',
                        'expected': f"{expected_owner}:{expected_group}:{expected_mode}",
                        'actual': f"{actual_owner}:{actual_group}:{actual_mode}"
                    })
                    drift_result['drift_detected'] = True
        
        return drift_result

# Example configuration files and demonstrations
def create_example_configs():
    """Create example configuration files."""
    
    # Server provisioning configuration
    provisioning_config = {
        'packages': {
            'install': ['nginx', 'postgresql', 'redis-server', 'git', 'curl'],
            'remove': ['apache2']
        },
        'services': {
            'nginx': {'state': 'started', 'enabled': True},
            'postgresql': {'state': 'started', 'enabled': True},
            'redis-server': {'state': 'started', 'enabled': True}
        },
        'users': {
            'appuser': {
                'shell': '/bin/bash',
                'home': '/home/appuser',
                'groups': ['www-data', 'sudo']
            }
        },
        'files': {
            '/etc/nginx/sites-available/myapp': {
                'content': '''server {
    listen 80;
    server_name example.com;
    root /var/www/myapp;
    index index.html;
    
    location / {
        try_files $uri $uri/ =404;
    }
}''',
                'owner': 'root',
                'group': 'root',
                'mode': '644'
            }
        },
        'firewall': {
            'reset': True,
            'default_incoming': 'deny',
            'default_outgoing': 'allow',
            'enabled': True,
            'rules': [
                {'action': 'allow', 'port': 22, 'protocol': 'tcp'},
                {'action': 'allow', 'port': 80, 'protocol': 'tcp'},
                {'action': 'allow', 'port': 443, 'protocol': 'tcp'},
                {'action': 'allow', 'port': 5432, 'protocol': 'tcp', 'source': '10.0.0.0/8'}
            ]
        },
        'cron': {
            'backup_database': {
                'schedule': '0 2 * * *',
                'command': '/usr/local/bin/backup_db.sh',
                'user': 'postgres'
            },
            'cleanup_logs': {
                'schedule': '0 3 * * 0',
                'command': 'find /var/log -name "*.log" -mtime +30 -delete',
                'user': 'root'
            }
        }
    }
    
    # Save configuration to file
    with open('server_config.yaml', 'w') as f:
        yaml.dump(provisioning_config, f, default_flow_style=False)
    
    print("Example configuration saved to server_config.yaml")

def demonstrate_automation_tasks():
    """Demonstrate automation task execution."""
    print("=== Automation Tasks Demo ===")
    
    config = ServerConfig(
        hostname="example.com",
        username="user",
        password="password"
    )
    
    try:
        with AdvancedSSHClient(config) as ssh:
            executor = TaskExecutor(ssh)
            
            # Define sample tasks
            tasks = [
                AutomationTask(
                    name="System Information",
                    description="Gather system information",
                    commands=[
                        "uname -a",
                        "df -h",
                        "free -h",
                        "uptime"
                    ]
                ),
                AutomationTask(
                    name="Update Package List",
                    description="Update package repository",
                    commands=["sudo apt-get update"],
                    prerequisites=["which apt-get"]
                ),
                AutomationTask(
                    name="Install Monitoring Tools",
                    description="Install system monitoring tools",
                    commands=[
                        "sudo apt-get install -y htop",
                        "sudo apt-get install -y iotop"
                    ],
                    rollback_commands=[
                        "sudo apt-get remove -y htop",
                        "sudo apt-get remove -y iotop"
                    ]
                )
            ]
            
            # Execute tasks
            results = executor.execute_task_sequence(tasks)
            
            # Display results
            for result in results:
                print(f"\nTask: {result.task_name}")
                print(f"Success: {result.success}")
                print(f"Execution Time: {result.execution_time:.2f}s")
                if result.output:
                    print(f"Output: {result.output[:200]}...")
                if result.error:
                    print(f"Error: {result.error}")
    
    except Exception as e:
        logger.error(f"Automation demo failed: {e}")

def demonstrate_server_provisioning():
    """Demonstrate server provisioning."""
    print("=== Server Provisioning Demo ===")
    
    config = ServerConfig(
        hostname="example.com",
        username="user",
        password="password"
    )
    
    try:
        with AdvancedSSHClient(config) as ssh:
            provisioner = ServerProvisioner(ssh)
            
            # Provision server with selected tasks
            provisioning_tasks = [
                'update_system',
                'install_monitoring',
                'setup_firewall'
            ]
            
            # Custom configuration
            custom_config = {
                'ssh_port': '2222',
                'admin_email': 'admin@example.com'
            }
            
            results = provisioner.provision_server(provisioning_tasks, custom_config)
            
            # Display results
            print("\nProvisioning Results:")
            for task_name, result in results.items():
                status = "SUCCESS" if result.success else "FAILED"
                print(f"{task_name}: {status}")
                if not result.success:
                    print(f"  Error: {result.error}")
    
    except Exception as e:
        logger.error(f"Provisioning demo failed: {e}")

def demonstrate_application_deployment():
    """Demonstrate application deployment."""
    print("=== Application Deployment Demo ===")
    
    ssh_config = ServerConfig(
        hostname="example.com",
        username="user",
        password="password"
    )
    
    # Deployment configuration
    deploy_config = DeploymentConfig(
        app_name="myapp",
        version="1.2.0",
        source_path="/local/path/to/app.tar.gz",
        target_path="/var/www/myapp",
        service_name="myapp",
        backup_enabled=True,
        health_check_url="http://localhost:8080/health",
        environment_vars={
            "APP_ENV": "production",
            "DB_HOST": "localhost",
            "DB_NAME": "myapp_db"
        },
        pre_deploy_tasks=[
            "sudo systemctl stop nginx",
            "sudo mkdir -p /var/www/myapp"
        ],
        post_deploy_tasks=[
            "sudo systemctl start nginx",
            "sudo systemctl reload nginx"
        ]
    )
    
    try:
        with AdvancedSSHClient(ssh_config) as ssh:
            deployer = ApplicationDeployer(ssh)
            
            # Deploy application
            deployment_result = deployer.deploy_application(deploy_config)
            
            # Display results
            print(f"\nDeployment ID: {deployment_result['deployment_id']}")
            print(f"Success: {deployment_result['success']}")
            print(f"Start Time: {deployment_result['start_time']}")
            print(f"End Time: {deployment_result.get('end_time', 'N/A')}")
            
            if deployment_result.get('rollback_performed'):
                print("Rollback was performed due to deployment failure")
            
            # Show task results
            print("\nTask Results:")
            for task_name, task_result in deployment_result.get('tasks', {}).items():
                if isinstance(task_result, list):
                    success_count = sum(1 for r in task_result if r.success)
                    print(f"  {task_name}: {success_count}/{len(task_result)} tasks succeeded")
                else:
                    status = "SUCCESS" if task_result.success else "FAILED"
                    print(f"  {task_name}: {status}")
    
    except Exception as e:
        logger.error(f"Deployment demo failed: {e}")

def demonstrate_configuration_management():
    """Demonstrate configuration management."""
    print("=== Configuration Management Demo ===")
    
    config = ServerConfig(
        hostname="example.com",
        username="user",
        password="password"
    )
    
    try:
        # Create example configuration
        create_example_configs()
        
        with AdvancedSSHClient(config) as ssh:
            config_manager = ConfigurationManager(ssh)
            
            # Apply configuration
            print("Applying configuration...")
            apply_result = config_manager.apply_configuration('server_config.yaml')
            
            print(f"Configuration application success: {apply_result['success']}")
            
            if apply_result['success']:
                print("Applied configurations:")
                for section, result in apply_result['applied_configs'].items():
                    print(f"  {section}: {'SUCCESS' if result['success'] else 'FAILED'}")
            
            if apply_result.get('errors'):
                print("Errors:")
                for error in apply_result['errors']:
                    print(f"  - {error}")
            
            # Detect configuration drift
            print("\nDetecting configuration drift...")
            drift_result = config_manager.detect_configuration_drift('server_config.yaml')
            
            print(f"Drift detected: {drift_result.get('drift_detected', False)}")
            
            if drift_result.get('drift_detected'):
                print("Drift details:")
                for section, section_drift in drift_result.get('sections', {}).items():
                    if section_drift.get('drift_detected'):
                        print(f"  {section}: Drift detected")
                        # Print specific drift details based on section type
                        if 'missing_packages' in section_drift:
                            missing = section_drift['missing_packages']
                            if missing:
                                print(f"    Missing packages: {', '.join(missing)}")
                        
                        if 'service_issues' in section_drift:
                            for issue in section_drift['service_issues']:
                                print(f"    Service {issue['service']}: "
                                     f"expected {issue['expected_state']}, "
                                     f"actual {issue['actual_state']}")
                        
                        if 'file_issues' in section_drift:
                            for issue in section_drift['file_issues']:
                                print(f"    File {issue['file']}: {issue['issue']}")
    
    except Exception as e:
        logger.error(f"Configuration management demo failed: {e}")

class AutomationOrchestrator:
    """
    Main orchestrator for automation workflows.
    Demonstrates workflow orchestration and complex automation scenarios.
    """
    
    def __init__(self, ssh_configs: List[ServerConfig]):
        self.ssh_configs = ssh_configs
        self.batch_operations = BatchSSHOperations(ssh_configs)
        self.workflows = {}
        
        # Register built-in workflows
        self._register_builtin_workflows()
    
    def _register_builtin_workflows(self):
        """Register built-in automation workflows."""
        self.workflows = {
            'full_server_setup': self._full_server_setup_workflow,
            'application_deployment': self._application_deployment_workflow,
            'security_hardening': self._security_hardening_workflow,
            'monitoring_setup': self._monitoring_setup_workflow,
            'backup_configuration': self._backup_configuration_workflow
        }
    
    def execute_workflow(self, workflow_name: str, config: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute a named workflow across multiple servers.
        
        Args:
            workflow_name: Name of the workflow to execute
            config: Workflow configuration parameters
            
        Returns:
            Dictionary with workflow execution results
        """
        if workflow_name not in self.workflows:
            return {
                'success': False,
                'error': f"Unknown workflow: {workflow_name}"
            }
        
        config = config or {}
        
        logger.info(f"Executing workflow '{workflow_name}' on {len(self.ssh_configs)} servers")
        
        workflow_result = {
            'workflow_name': workflow_name,
            'start_time': datetime.now(),
            'success': True,
            'server_results': {},
            'summary': {
                'total_servers': len(self.ssh_configs),
                'successful_servers': 0,
                'failed_servers': 0
            }
        }
        
        try:
            # Execute workflow function
            workflow_function = self.workflows[workflow_name]
            server_results = workflow_function(config)
            
            workflow_result['server_results'] = server_results
            
            # Calculate summary
            for hostname, result in server_results.items():
                if result.get('success', False):
                    workflow_result['summary']['successful_servers'] += 1
                else:
                    workflow_result['summary']['failed_servers'] += 1
                    workflow_result['success'] = False
            
            workflow_result['end_time'] = datetime.now()
            
        except Exception as e:
            workflow_result['success'] = False
            workflow_result['error'] = str(e)
            workflow_result['end_time'] = datetime.now()
            logger.error(f"Workflow '{workflow_name}' failed: {e}")
        
        return workflow_result
    
    def _full_server_setup_workflow(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Complete server setup workflow."""
        results = {}
        
        # Define setup tasks
        setup_tasks = [
            'update_system',
            'install_docker',
            'setup_firewall',
            'configure_ssh',
            'install_monitoring',
            'setup_backup'
        ]
        
        # Execute on each server
        for ssh_config in self.ssh_configs:
            try:
                with AdvancedSSHClient(ssh_config) as ssh:
                    provisioner = ServerProvisioner(ssh)
                    
                    # Provision server
                    provision_results = provisioner.provision_server(setup_tasks, config)
                    
                    # Apply configuration if provided
                    if config.get('config_file'):
                        config_manager = ConfigurationManager(ssh)
                        config_result = config_manager.apply_configuration(config['config_file'])
                        provision_results['configuration'] = config_result
                    
                    # Determine overall success
                    success = all(r.success for r in provision_results.values() 
                                if hasattr(r, 'success'))
                    
                    results[ssh_config.hostname] = {
                        'success': success,
                        'tasks': provision_results
                    }
                    
            except Exception as e:
                results[ssh_config.hostname] = {
                    'success': False,
                    'error': str(e)
                }
        
        return results
    
    def _application_deployment_workflow(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Application deployment workflow."""
        results = {}
        
        # Validate required configuration
        required_fields = ['app_name', 'version', 'source_path', 'target_path', 'service_name']
        for field in required_fields:
            if field not in config:
                return {
                    'error': f"Missing required configuration field: {field}"
                }
        
        # Create deployment configuration
        deploy_config = DeploymentConfig(**config)
        
        # Execute deployment on each server
        for ssh_config in self.ssh_configs:
            try:
                with AdvancedSSHClient(ssh_config) as ssh:
                    deployer = ApplicationDeployer(ssh)
                    
                    # Deploy application
                    deployment_result = deployer.deploy_application(deploy_config)
                    
                    results[ssh_config.hostname] = deployment_result
                    
            except Exception as e:
                results[ssh_config.hostname] = {
                    'success': False,
                    'error': str(e)
                }
        
        return results
    
    def _security_hardening_workflow(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Security hardening workflow."""
        results = {}
        
        # Define security hardening tasks
        security_tasks = [
            AutomationTask(
                name="Update System",
                description="Update all system packages",
                commands=[
                    "sudo apt-get update",
                    "sudo apt-get upgrade -y"
                ]
            ),
            AutomationTask(
                name="Configure SSH",
                description="Harden SSH configuration",
                commands=[
                    "sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup",
                    "sudo sed -i 's/#PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config",
                    "sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config",
                    "sudo systemctl restart sshd"
                ]
            ),
            AutomationTask(
                name="Setup Firewall",
                description="Configure UFW firewall",
                commands=[
                    "sudo ufw --force reset",
                    "sudo ufw default deny incoming",
                    "sudo ufw default allow outgoing",
                    "sudo ufw allow ssh",
                    "sudo ufw --force enable"
                ]
            ),
            AutomationTask(
                name="Install Security Tools",
                description="Install security monitoring tools",
                commands=[
                    "sudo apt-get install -y fail2ban",
                    "sudo apt-get install -y rkhunter",
                    "sudo systemctl enable fail2ban",
                    "sudo systemctl start fail2ban"
                ]
            ),
            AutomationTask(
                name="Configure Automatic Updates",
                description="Enable automatic security updates",
                commands=[
                    "sudo apt-get install -y unattended-upgrades",
                    "sudo dpkg-reconfigure -plow unattended-upgrades"
                ]
            )
        ]
        
        # Execute on each server
        for ssh_config in self.ssh_configs:
            try:
                with AdvancedSSHClient(ssh_config) as ssh:
                    executor = TaskExecutor(ssh)
                    
                    # Execute security tasks
                    task_results = executor.execute_task_sequence(security_tasks)
                    
                    # Determine overall success
                    success = all(r.success for r in task_results)
                    
                    results[ssh_config.hostname] = {
                        'success': success,
                        'tasks': [
                            {
                                'name': r.task_name,
                                'success': r.success,
                                'error': r.error if not r.success else None
                            }
                            for r in task_results
                        ]
                    }
                    
            except Exception as e:
                results[ssh_config.hostname] = {
                    'success': False,
                    'error': str(e)
                }
        
        return results
    
    def _monitoring_setup_workflow(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Monitoring setup workflow."""
        results = {}
        
        # Define monitoring setup tasks
        monitoring_tasks = [
            AutomationTask(
                name="Install Monitoring Tools",
                description="Install system monitoring tools",
                commands=[
                    "sudo apt-get update",
                    "sudo apt-get install -y htop iotop nethogs",
                    "sudo apt-get install -y sysstat",
                    "sudo systemctl enable sysstat",
                    "sudo systemctl start sysstat"
                ]
            ),
            AutomationTask(
                name="Setup Log Rotation",
                description="Configure log rotation",
                commands=[
                    "sudo apt-get install -y logrotate",
                    "sudo systemctl enable logrotate.timer",
                    "sudo systemctl start logrotate.timer"
                ]
            ),
            AutomationTask(
                name="Configure System Monitoring",
                description="Setup system monitoring scripts",
                commands=[
                    "sudo mkdir -p /opt/monitoring",
                    "echo '#!/bin/bash' | sudo tee /opt/monitoring/system_check.sh",
                    "echo 'df -h > /var/log/disk_usage.log' | sudo tee -a /opt/monitoring/system_check.sh",
                    "echo 'free -h > /var/log/memory_usage.log' | sudo tee -a /opt/monitoring/system_check.sh",
                    "sudo chmod +x /opt/monitoring/system_check.sh",
                    "echo '*/5 * * * * /opt/monitoring/system_check.sh' | sudo crontab -"
                ]
            )
        ]
        
        # Execute on each server
        for ssh_config in self.ssh_configs:
            try:
                with AdvancedSSHClient(ssh_config) as ssh:
                    executor = TaskExecutor(ssh)
                    
                    # Execute monitoring tasks
                    task_results = executor.execute_task_sequence(monitoring_tasks)
                    
                    # Determine overall success
                    success = all(r.success for r in task_results)
                    
                    results[ssh_config.hostname] = {
                        'success': success,
                        'tasks': [
                            {
                                'name': r.task_name,
                                'success': r.success,
                                'execution_time': r.execution_time
                            }
                            for r in task_results
                        ]
                    }
                    
            except Exception as e:
                results[ssh_config.hostname] = {
                    'success': False,
                    'error': str(e)
                }
        
        return results
    
    def _backup_configuration_workflow(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Backup configuration workflow."""
        results = {}
        
        backup_paths = config.get('backup_paths', ['/etc', '/home', '/var/www'])
        backup_destination = config.get('backup_destination', '/backup')
        
        # Define backup setup tasks
        backup_tasks = [
            AutomationTask(
                name="Create Backup Directory",
                description="Create backup directory structure",
                commands=[
                    f"sudo mkdir -p {backup_destination}/scripts",
                    f"sudo mkdir -p {backup_destination}/data",
                    f"sudo chown $USER:$USER {backup_destination}"
                ]
            ),
            AutomationTask(
                name="Create Backup Script",
                description="Create automated backup script",
                commands=[
                    f"echo '#!/bin/bash' | sudo tee {backup_destination}/scripts/backup.sh",
                    f"echo 'BACKUP_DATE=$(date +%Y%m%d_%H%M%S)' | sudo tee -a {backup_destination}/scripts/backup.sh",
                    f"echo 'tar -czf {backup_destination}/data/backup_$BACKUP_DATE.tar.gz {' '.join(backup_paths)}' | sudo tee -a {backup_destination}/scripts/backup.sh",
                    f"echo 'find {backup_destination}/data -name \"backup_*.tar.gz\" -mtime +7 -delete' | sudo tee -a {backup_destination}/scripts/backup.sh",
                    f"sudo chmod +x {backup_destination}/scripts/backup.sh"
                ]
            ),
            AutomationTask(
                name="Schedule Backup",
                description="Schedule daily backup via cron",
                commands=[
                    f"echo '0 2 * * * {backup_destination}/scripts/backup.sh' | crontab -",
                    "crontab -l"
                ]
            ),
            AutomationTask(
                name="Test Backup",
                description="Run initial backup test",
                commands=[
                    f"{backup_destination}/scripts/backup.sh",
                    f"ls -la {backup_destination}/data/"
                ]
            )
        ]
        
        # Execute on each server
        for ssh_config in self.ssh_configs:
            try:
                with AdvancedSSHClient(ssh_config) as ssh:
                    executor = TaskExecutor(ssh)
                    
                    # Execute backup tasks
                    task_results = executor.execute_task_sequence(backup_tasks)
                    
                    # Determine overall success
                    success = all(r.success for r in task_results)
                    
                    results[ssh_config.hostname] = {
                        'success': success,
                        'backup_destination': backup_destination,
                        'backup_paths': backup_paths,
                        'tasks': [
                            {
                                'name': r.task_name,
                                'success': r.success,
                                'execution_time': r.execution_time
                            }
                            for r in task_results
                        ]
                    }
                    
            except Exception as e:
                results[ssh_config.hostname] = {
                    'success': False,
                    'error': str(e)
                }
        
        return results

def demonstrate_automation_orchestrator():
    """Demonstrate automation orchestrator capabilities."""
    print("=== Automation Orchestrator Demo ===")
    
    # Define multiple server configurations
    server_configs = [
        ServerConfig(hostname="server1.example.com", username="user", password="password"),
        ServerConfig(hostname="server2.example.com", username="user", password="password"),
        ServerConfig(hostname="server3.example.com", username="user", password="password")
    ]
    
    try:
        orchestrator = AutomationOrchestrator(server_configs)
        
        # Execute security hardening workflow
        print("Executing security hardening workflow...")
        security_result = orchestrator.execute_workflow('security_hardening')
        
        print(f"Workflow success: {security_result['success']}")
        print(f"Successful servers: {security_result['summary']['successful_servers']}")
        print(f"Failed servers: {security_result['summary']['failed_servers']}")
        
        # Show per-server results
        for hostname, result in security_result['server_results'].items():
            print(f"\n{hostname}: {'SUCCESS' if result['success'] else 'FAILED'}")
            if not result['success'] and 'error' in result:
                print(f"  Error: {result['error']}")
        
        # Execute monitoring setup workflow
        print("\nExecuting monitoring setup workflow...")
        monitoring_result = orchestrator.execute_workflow('monitoring_setup')
        
        print(f"Monitoring setup success: {monitoring_result['success']}")
        
        # Execute backup configuration workflow
        print("\nExecuting backup configuration workflow...")
        backup_config = {
            'backup_paths': ['/etc', '/home'],
            'backup_destination': '/backup'
        }
        backup_result = orchestrator.execute_workflow('backup_configuration', backup_config)
        
        print(f"Backup configuration success: {backup_result['success']}")
    
    except Exception as e:
        logger.error(f"Orchestrator demo failed: {e}")

def main():
    """Main demonstration function."""
    print("=== Unix Server Automation Examples ===")
    
    print("\n1. Automation Tasks Demo")
    print("   (Requires server credentials - modify demonstrate_automation_tasks())")
    # demonstrate_automation_tasks()
    
    print("\n2. Server Provisioning Demo")
    print("   (Requires server credentials - modify demonstrate_server_provisioning())")
    # demonstrate_server_provisioning()
    
    print("\n3. Application Deployment Demo")
    print("   (Requires server credentials - modify demonstrate_application_deployment())")
    # demonstrate_application_deployment()
    
    print("\n4. Configuration Management Demo")
    print("   (Requires server credentials - modify demonstrate_configuration_management())")
    # demonstrate_configuration_management()
    
    print("\n5. Automation Orchestrator Demo")
    print("   (Requires server credentials - modify demonstrate_automation_orchestrator())")
    # demonstrate_automation_orchestrator()
    
    print("\nTo use these examples:")
    print("1. Install dependencies: pip install paramiko pyyaml")
    print("2. Modify server credentials in the demo functions")
    print("3. Create configuration files using create_example_configs()")
    print("4. Run the appropriate demonstration function")
    
    print("\nAutomation features demonstrated:")
    print("- Task execution with retry logic and rollback")
    print("- Server provisioning and configuration")
    print("- Application deployment with health checks")
    print("- Configuration management and drift detection")
    print("- Multi-server workflow orchestration")
    print("- Infrastructure as code principles")
    print("- Error handling and recovery strategies")
    print("- Automated backup and monitoring setup")

if __name__ == "__main__":
    main()
