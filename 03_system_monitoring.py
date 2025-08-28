"""
Unix System Monitoring and Health Checks
This module demonstrates system monitoring, log analysis, and health checking
for Unix servers. Showcases Python concepts like data analysis, scheduling,
alerting, and real-time monitoring.
"""

import re
import time
import json
import sqlite3
import smtplib
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, NamedTuple
from dataclasses import dataclass, field
from collections import defaultdict, deque
from email.mime.text import MimeText
from email.mime.multipart import MimeMultipart
import schedule
import psutil
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path

from advanced_ssh_operations import AdvancedSSHClient, ServerConfig, CommandResult, logger

@dataclass
class SystemMetrics:
    """System performance metrics data structure."""
    timestamp: datetime
    hostname: str
    cpu_usage: float
    memory_usage: float
    disk_usage: float
    load_average: Tuple[float, float, float]
    network_io: Dict[str, int]
    disk_io: Dict[str, int]
    process_count: int
    uptime_seconds: int
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'timestamp': self.timestamp.isoformat(),
            'hostname': self.hostname,
            'cpu_usage': self.cpu_usage,
            'memory_usage': self.memory_usage,
            'disk_usage': self.disk_usage,
            'load_average': self.load_average,
            'network_io': self.network_io,
            'disk_io': self.disk_io,
            'process_count': self.process_count,
            'uptime_seconds': self.uptime_seconds
        }

@dataclass
class AlertRule:
    """Alert rule configuration."""
    name: str
    metric: str
    threshold: float
    operator: str  # '>', '<', '>=', '<=', '==', '!='
    duration_minutes: int = 5
    enabled: bool = True
    
    def check_condition(self, value: float) -> bool:
        """Check if the alert condition is met."""
        if not self.enabled:
            return False
        
        operators = {
            '>': lambda x, y: x > y,
            '<': lambda x, y: x < y,
            '>=': lambda x, y: x >= y,
            '<=': lambda x, y: x <= y,
            '==': lambda x, y: x == y,
            '!=': lambda x, y: x != y
        }
        
        return operators.get(self.operator, lambda x, y: False)(value, self.threshold)

@dataclass
class Alert:
    """Active alert information."""
    rule_name: str
    hostname: str
    metric: str
    current_value: float
    threshold: float
    triggered_at: datetime
    resolved_at: Optional[datetime] = None
    
    @property
    def is_active(self) -> bool:
        """Check if alert is still active."""
        return self.resolved_at is None
    
    @property
    def duration(self) -> timedelta:
        """Get alert duration."""
        end_time = self.resolved_at or datetime.now()
        return end_time - self.triggered_at

class SystemMonitor:
    """
    Comprehensive system monitoring class.
    Demonstrates real-time monitoring, data collection, and alerting.
    """
    
    def __init__(self, ssh_client: AdvancedSSHClient):
        self.ssh_client = ssh_client
        self.metrics_history: deque = deque(maxlen=1000)  # Keep last 1000 metrics
        self.alert_rules: List[AlertRule] = []
        self.active_alerts: Dict[str, Alert] = {}
        self.monitoring_active = False
        self.monitor_thread: Optional[threading.Thread] = None
        
        # Default alert rules
        self._setup_default_alerts()
    
    def _setup_default_alerts(self):
        """Setup default monitoring alert rules."""
        default_rules = [
            AlertRule("High CPU Usage", "cpu_usage", 80.0, ">", 5),
            AlertRule("High Memory Usage", "memory_usage", 85.0, ">", 5),
            AlertRule("High Disk Usage", "disk_usage", 90.0, ">", 10),
            AlertRule("High Load Average", "load_average_1", 4.0, ">", 3),
            AlertRule("Low Disk Space", "disk_usage", 95.0, ">", 1),
        ]
        self.alert_rules.extend(default_rules)
    
    def collect_metrics(self) -> Optional[SystemMetrics]:
        """
        Collect system metrics from the remote server.
        
        Returns:
            SystemMetrics object or None if collection fails
        """
        try:
            # Collect various system metrics using multiple commands
            commands = {
                'cpu': "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1",
                'memory': "free | grep Mem | awk '{printf \"%.1f\", $3/$2 * 100.0}'",
                'disk': "df -h / | awk 'NR==2 {print $5}' | cut -d'%' -f1",
                'load': "uptime | awk -F'load average:' '{print $2}' | sed 's/,//g'",
                'processes': "ps aux | wc -l",
                'uptime': "cat /proc/uptime | awk '{print $1}'",
                'hostname': "hostname",
                'network_io': "cat /proc/net/dev | grep -E '(eth0|ens|enp)' | head -1 | awk '{print $2,$10}'",
                'disk_io': "cat /proc/diskstats | grep -E '(sda|nvme)' | head -1 | awk '{print $6,$10}'"
            }
            
            results = {}
            for name, command in commands.items():
                result = self.ssh_client.execute_command(command)
                if result.success:
                    results[name] = result.stdout.strip()
                else:
                    logger.warning(f"Failed to collect {name}: {result.stderr}")
                    return None
            
            # Parse results into SystemMetrics
            try:
                load_parts = results['load'].split()
                load_average = (
                    float(load_parts[0]) if len(load_parts) > 0 else 0.0,
                    float(load_parts[1]) if len(load_parts) > 1 else 0.0,
                    float(load_parts[2]) if len(load_parts) > 2 else 0.0
                )
                
                network_parts = results['network_io'].split()
                network_io = {
                    'bytes_received': int(network_parts[0]) if len(network_parts) > 0 else 0,
                    'bytes_sent': int(network_parts[1]) if len(network_parts) > 1 else 0
                }
                
                disk_parts = results['disk_io'].split()
                disk_io = {
                    'reads': int(disk_parts[0]) if len(disk_parts) > 0 else 0,
                    'writes': int(disk_parts[1]) if len(disk_parts) > 1 else 0
                }
                
                metrics = SystemMetrics(
                    timestamp=datetime.now(),
                    hostname=results['hostname'],
                    cpu_usage=float(results['cpu']),
                    memory_usage=float(results['memory']),
                    disk_usage=float(results['disk']),
                    load_average=load_average,
                    network_io=network_io,
                    disk_io=disk_io,
                    process_count=int(results['processes']),
                    uptime_seconds=int(float(results['uptime']))
                )
                
                return metrics
                
            except (ValueError, IndexError) as e:
                logger.error(f"Failed to parse metrics: {e}")
                return None
                
        except Exception as e:
            logger.error(f"Metrics collection failed: {e}")
            return None
    
    def check_alerts(self, metrics: SystemMetrics) -> List[Alert]:
        """
        Check alert rules against current metrics.
        
        Args:
            metrics: Current system metrics
            
        Returns:
            List of triggered alerts
        """
        triggered_alerts = []
        
        for rule in self.alert_rules:
            if not rule.enabled:
                continue
            
            # Get metric value
            metric_value = self._get_metric_value(metrics, rule.metric)
            if metric_value is None:
                continue
            
            # Check if condition is met
            if rule.check_condition(metric_value):
                alert_key = f"{metrics.hostname}_{rule.name}"
                
                if alert_key not in self.active_alerts:
                    # New alert
                    alert = Alert(
                        rule_name=rule.name,
                        hostname=metrics.hostname,
                        metric=rule.metric,
                        current_value=metric_value,
                        threshold=rule.threshold,
                        triggered_at=datetime.now()
                    )
                    self.active_alerts[alert_key] = alert
                    triggered_alerts.append(alert)
                    logger.warning(f"Alert triggered: {rule.name} on {metrics.hostname}")
            else:
                # Check if we should resolve an existing alert
                alert_key = f"{metrics.hostname}_{rule.name}"
                if alert_key in self.active_alerts:
                    self.active_alerts[alert_key].resolved_at = datetime.now()
                    logger.info(f"Alert resolved: {rule.name} on {metrics.hostname}")
                    del self.active_alerts[alert_key]
        
        return triggered_alerts
    
    def _get_metric_value(self, metrics: SystemMetrics, metric_name: str) -> Optional[float]:
        """Get metric value by name."""
        metric_map = {
            'cpu_usage': metrics.cpu_usage,
            'memory_usage': metrics.memory_usage,
            'disk_usage': metrics.disk_usage,
            'load_average_1': metrics.load_average[0],
            'load_average_5': metrics.load_average[1],
            'load_average_15': metrics.load_average[2],
            'process_count': float(metrics.process_count),
            'uptime_seconds': float(metrics.uptime_seconds)
        }
        return metric_map.get(metric_name)
    
    def start_monitoring(self, interval_seconds: int = 60):
        """
        Start continuous monitoring in a background thread.
        
        Args:
            interval_seconds: Monitoring interval in seconds
        """
        if self.monitoring_active:
            logger.warning("Monitoring is already active")
            return
        
        self.monitoring_active = True
        
        def monitor_loop():
            logger.info(f"Starting system monitoring with {interval_seconds}s interval")
            
            while self.monitoring_active:
                try:
                    # Collect metrics
                    metrics = self.collect_metrics()
                    if metrics:
                        self.metrics_history.append(metrics)
                        
                        # Check alerts
                        triggered_alerts = self.check_alerts(metrics)
                        
                        # Log current status
                        logger.info(f"Metrics collected for {metrics.hostname}: "
                                  f"CPU={metrics.cpu_usage:.1f}%, "
                                  f"Memory={metrics.memory_usage:.1f}%, "
                                  f"Disk={metrics.disk_usage:.1f}%")
                    
                    time.sleep(interval_seconds)
                    
                except Exception as e:
                    logger.error(f"Monitoring loop error: {e}")
                    time.sleep(interval_seconds)
        
        self.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitor_thread.start()
    
    def stop_monitoring(self):
        """Stop continuous monitoring."""
        self.monitoring_active = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("System monitoring stopped")
    
    def get_metrics_summary(self, hours: int = 24) -> Dict[str, Any]:
        """
        Get metrics summary for the specified time period.
        
        Args:
            hours: Number of hours to include in summary
            
        Returns:
            Dictionary with metrics summary
        """
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_metrics = [m for m in self.metrics_history if m.timestamp >= cutoff_time]
        
        if not recent_metrics:
            return {}
        
        # Calculate statistics
        cpu_values = [m.cpu_usage for m in recent_metrics]
        memory_values = [m.memory_usage for m in recent_metrics]
        disk_values = [m.disk_usage for m in recent_metrics]
        
        return {
            'period_hours': hours,
            'data_points': len(recent_metrics),
            'cpu_usage': {
                'avg': sum(cpu_values) / len(cpu_values),
                'min': min(cpu_values),
                'max': max(cpu_values)
            },
            'memory_usage': {
                'avg': sum(memory_values) / len(memory_values),
                'min': min(memory_values),
                'max': max(memory_values)
            },
            'disk_usage': {
                'avg': sum(disk_values) / len(disk_values),
                'min': min(disk_values),
                'max': max(disk_values)
            },
            'active_alerts': len(self.active_alerts),
            'hostname': recent_metrics[0].hostname if recent_metrics else 'unknown'
        }

class LogAnalyzer:
    """
    Log file analysis and monitoring.
    Demonstrates text processing, pattern matching, and log analysis.
    """
    
    def __init__(self, ssh_client: AdvancedSSHClient):
        self.ssh_client = ssh_client
        self.log_patterns = {
            'error': [
                r'ERROR',
                r'CRITICAL',
                r'FATAL',
                r'Exception',
                r'Traceback'
            ],
            'warning': [
                r'WARNING',
                r'WARN',
                r'deprecated'
            ],
            'security': [
                r'authentication failure',
                r'invalid user',
                r'failed login',
                r'sudo.*COMMAND',
                r'su.*session'
            ],
            'network': [
                r'connection refused',
                r'timeout',
                r'network unreachable',
                r'dns.*failed'
            ]
        }
    
    def analyze_log_file(self, log_path: str, lines: int = 1000) -> Dict[str, Any]:
        """
        Analyze a log file for patterns and issues.
        
        Args:
            log_path: Path to the log file on remote server
            lines: Number of recent lines to analyze
            
        Returns:
            Dictionary with analysis results
        """
        try:
            # Get recent log lines
            result = self.ssh_client.execute_command(f"tail -n {lines} {log_path}")
            if not result.success:
                logger.error(f"Failed to read log file {log_path}: {result.stderr}")
                return {}
            
            log_content = result.stdout
            log_lines = log_content.split('\n')
            
            analysis = {
                'file_path': log_path,
                'lines_analyzed': len(log_lines),
                'patterns_found': defaultdict(list),
                'summary': defaultdict(int),
                'recent_errors': [],
                'timestamps': []
            }
            
            # Analyze each line
            for line_num, line in enumerate(log_lines, 1):
                if not line.strip():
                    continue
                
                # Extract timestamp if present
                timestamp_match = re.search(r'\d{4}-\d{2}-\d{2}[\s\T]\d{2}:\d{2}:\d{2}', line)
                if timestamp_match:
                    analysis['timestamps'].append(timestamp_match.group())
                
                # Check for patterns
                for category, patterns in self.log_patterns.items():
                    for pattern in patterns:
                        if re.search(pattern, line, re.IGNORECASE):
                            analysis['patterns_found'][category].append({
                                'line_number': line_num,
                                'content': line.strip(),
                                'pattern': pattern
                            })
                            analysis['summary'][category] += 1
                            
                            # Keep recent errors for detailed reporting
                            if category == 'error' and len(analysis['recent_errors']) < 10:
                                analysis['recent_errors'].append(line.strip())
            
            return dict(analysis)
            
        except Exception as e:
            logger.error(f"Log analysis failed: {e}")
            return {}
    
    def monitor_log_growth(self, log_path: str, interval_seconds: int = 300) -> Dict[str, Any]:
        """
        Monitor log file growth rate and detect rapid growth.
        
        Args:
            log_path: Path to the log file
            interval_seconds: Monitoring interval
            
        Returns:
            Dictionary with growth statistics
        """
        try:
            # Get initial file size
            result = self.ssh_client.execute_command(f"stat -c%s {log_path}")
            if not result.success:
                return {}
            
            initial_size = int(result.stdout.strip())
            initial_time = time.time()
            
            # Wait for interval
            time.sleep(interval_seconds)
            
            # Get final file size
            result = self.ssh_client.execute_command(f"stat -c%s {log_path}")
            if not result.success:
                return {}
            
            final_size = int(result.stdout.strip())
            final_time = time.time()
            
            # Calculate growth rate
            size_diff = final_size - initial_size
            time_diff = final_time - initial_time
            growth_rate = size_diff / time_diff if time_diff > 0 else 0
            
            return {
                'log_path': log_path,
                'initial_size': initial_size,
                'final_size': final_size,
                'size_growth': size_diff,
                'time_interval': time_diff,
                'growth_rate_bytes_per_second': growth_rate,
                'growth_rate_mb_per_hour': (growth_rate * 3600) / (1024 * 1024)
            }
            
        except Exception as e:
            logger.error(f"Log growth monitoring failed: {e}")
            return {}
    
    def search_log_patterns(self, log_path: str, patterns: List[str], 
                           context_lines: int = 2) -> Dict[str, List[Dict]]:
        """
        Search for specific patterns in log files with context.
        
        Args:
            log_path: Path to the log file
            patterns: List of regex patterns to search for
            context_lines: Number of context lines to include
            
        Returns:
            Dictionary mapping patterns to found matches
        """
        results = {}
        
        for pattern in patterns:
            try:
                # Use grep to search for pattern with context
                command = f"grep -n -A{context_lines} -B{context_lines} '{pattern}' {log_path}"
                result = self.ssh_client.execute_command(command)
                
                if result.success and result.stdout:
                    matches = []
                    current_match = []
                    
                    for line in result.stdout.split('\n'):
                        if line.strip():
                            if line.startswith('--'):
                                if current_match:
                                    matches.append('\n'.join(current_match))
                                    current_match = []
                            else:
                                current_match.append(line)
                    
                    if current_match:
                        matches.append('\n'.join(current_match))
                    
                    results[pattern] = matches
                else:
                    results[pattern] = []
                    
            except Exception as e:
                logger.error(f"Pattern search failed for '{pattern}': {e}")
                results[pattern] = []
        
        return results

class HealthChecker:
    """
    Comprehensive health checking for Unix systems.
    Demonstrates system diagnostics and health assessment.
    """
    
    def __init__(self, ssh_client: AdvancedSSHClient):
        self.ssh_client = ssh_client
        self.health_checks = {
            'disk_space': self._check_disk_space,
            'memory_usage': self._check_memory_usage,
            'cpu_load': self._check_cpu_load,
            'running_services': self._check_running_services,
            'network_connectivity': self._check_network_connectivity,
            'system_updates': self._check_system_updates,
            'log_errors': self._check_log_errors,
            'file_permissions': self._check_file_permissions
        }
    
    def run_health_check(self, checks: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Run comprehensive health checks.
        
        Args:
            checks: List of specific checks to run (None for all)
            
        Returns:
            Dictionary with health check results
        """
        if checks is None:
            checks = list(self.health_checks.keys())
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'hostname': self._get_hostname(),
            'checks': {},
            'overall_status': 'UNKNOWN',
            'critical_issues': [],
            'warnings': [],
            'recommendations': []
        }
        
        passed_checks = 0
        total_checks = len(checks)
        
        for check_name in checks:
            if check_name in self.health_checks:
                try:
                    check_result = self.health_checks[check_name]()
                    results['checks'][check_name] = check_result
                    
                    if check_result['status'] == 'PASS':
                        passed_checks += 1
                    elif check_result['status'] == 'CRITICAL':
                        results['critical_issues'].append(check_result['message'])
                    elif check_result['status'] == 'WARNING':
                        results['warnings'].append(check_result['message'])
                    
                    if 'recommendation' in check_result:
                        results['recommendations'].append(check_result['recommendation'])
                        
                except Exception as e:
                    logger.error(f"Health check '{check_name}' failed: {e}")
                    results['checks'][check_name] = {
                        'status': 'ERROR',
                        'message': str(e)
                    }
        
        # Determine overall status
        if results['critical_issues']:
            results['overall_status'] = 'CRITICAL'
        elif results['warnings']:
            results['overall_status'] = 'WARNING'
        elif passed_checks == total_checks:
            results['overall_status'] = 'HEALTHY'
        else:
            results['overall_status'] = 'DEGRADED'
        
        return results
    
    def _get_hostname(self) -> str:
        """Get system hostname."""
        try:
            result = self.ssh_client.execute_command("hostname")
            return result.stdout.strip() if result.success else 'unknown'
        except:
            return 'unknown'
    
    def _check_disk_space(self) -> Dict[str, Any]:
        """Check disk space usage."""
        try:
            result = self.ssh_client.execute_command("df -h | grep -vE '^Filesystem|tmpfs|cdrom'")
            if not result.success:
                return {'status': 'ERROR', 'message': 'Failed to check disk space'}
            
            critical_mounts = []
            warning_mounts = []
            
            for line in result.stdout.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 5:
                        mount_point = parts[-1]
                        usage_str = parts[-2]
                        if usage_str.endswith('%'):
                            usage = int(usage_str[:-1])
                            
                            if usage >= 95:
                                critical_mounts.append(f"{mount_point}: {usage}%")
                            elif usage >= 85:
                                warning_mounts.append(f"{mount_point}: {usage}%")
            
            if critical_mounts:
                return {
                    'status': 'CRITICAL',
                    'message': f"Critical disk usage: {', '.join(critical_mounts)}",
                    'recommendation': 'Free up disk space immediately'
                }
            elif warning_mounts:
                return {
                    'status': 'WARNING',
                    'message': f"High disk usage: {', '.join(warning_mounts)}",
                    'recommendation': 'Monitor disk usage and plan cleanup'
                }
            else:
                return {'status': 'PASS', 'message': 'Disk space usage is normal'}
                
        except Exception as e:
            return {'status': 'ERROR', 'message': f"Disk space check failed: {e}"}
    
    def _check_memory_usage(self) -> Dict[str, Any]:
        """Check memory usage."""
        try:
            result = self.ssh_client.execute_command("free | grep Mem")
            if not result.success:
                return {'status': 'ERROR', 'message': 'Failed to check memory usage'}
            
            parts = result.stdout.split()
            if len(parts) >= 3:
                total = int(parts[1])
                used = int(parts[2])
                usage_percent = (used / total) * 100
                
                if usage_percent >= 95:
                    return {
                        'status': 'CRITICAL',
                        'message': f"Critical memory usage: {usage_percent:.1f}%",
                        'recommendation': 'Investigate memory-intensive processes'
                    }
                elif usage_percent >= 85:
                    return {
                        'status': 'WARNING',
                        'message': f"High memory usage: {usage_percent:.1f}%",
                        'recommendation': 'Monitor memory usage trends'
                    }
                else:
                    return {'status': 'PASS', 'message': f"Memory usage is normal: {usage_percent:.1f}%"}
            
            return {'status': 'ERROR', 'message': 'Could not parse memory information'}
            
        except Exception as e:
            return {'status': 'ERROR', 'message': f"Memory check failed: {e}"}
    
    def _check_cpu_load(self) -> Dict[str, Any]:
        """Check CPU load average."""
        try:
            result = self.ssh_client.execute_command("uptime")
            if not result.success:
                return {'status': 'ERROR', 'message': 'Failed to check CPU load'}
            
            # Extract load average
            load_match = re.search(r'load average:\s*([\d.]+)', result.stdout)
            if load_match:
                load_1min = float(load_match.group(1))
                
                # Get number of CPU cores
                cpu_result = self.ssh_client.execute_command("nproc")
                cpu_cores = int(cpu_result.stdout.strip()) if cpu_result.success else 1
                
                load_per_core = load_1min / cpu_cores
                
                if load_per_core >= 2.0:
                    return {
                        'status': 'CRITICAL',
                        'message': f"Critical CPU load: {load_1min} (load per core: {load_per_core:.2f})",
                        'recommendation': 'Investigate high CPU usage processes'
                    }
                elif load_per_core >= 1.5:
                    return {
                        'status': 'WARNING',
                        'message': f"High CPU load: {load_1min} (load per core: {load_per_core:.2f})",
                        'recommendation': 'Monitor CPU usage trends'
                    }
                else:
                    return {'status': 'PASS', 'message': f"CPU load is normal: {load_1min}"}
            
            return {'status': 'ERROR', 'message': 'Could not parse load average'}
            
        except Exception as e:
            return {'status': 'ERROR', 'message': f"CPU load check failed: {e}"}
    
    def _check_running_services(self) -> Dict[str, Any]:
        """Check critical services status."""
        critical_services = ['sshd', 'cron', 'rsyslog']
        
        try:
            failed_services = []
            
            for service in critical_services:
                result = self.ssh_client.execute_command(f"systemctl is-active {service}")
                if not result.success or result.stdout.strip() != 'active':
                    failed_services.append(service)
            
            if failed_services:
                return {
                    'status': 'CRITICAL',
                    'message': f"Critical services not running: {', '.join(failed_services)}",
                    'recommendation': f"Start failed services: {', '.join(failed_services)}"
                }
            else:
                return {'status': 'PASS', 'message': 'All critical services are running'}
                
        except Exception as e:
            return {'status': 'ERROR', 'message': f"Service check failed: {e}"}
    
    def _check_network_connectivity(self) -> Dict[str, Any]:
        """Check network connectivity."""
        try:
            # Test DNS resolution and connectivity
            result = self.ssh_client.execute_command("ping -c 3 8.8.8.8")
            if result.success and "3 received" in result.stdout:
                return {'status': 'PASS', 'message': 'Network connectivity is working'}
            else:
                return {
                    'status': 'CRITICAL',
                    'message': 'Network connectivity issues detected',
                    'recommendation': 'Check network configuration and routing'
                }
                
        except Exception as e:
            return {'status': 'ERROR', 'message': f"Network check failed: {e}"}
    
    def _check_system_updates(self) -> Dict[str, Any]:
        """Check for available system updates."""
        try:
            # Check for updates (works on Ubuntu/Debian)
            result = self.ssh_client.execute_command("apt list --upgradable 2>/dev/null | wc -l")
            if result.success:
                update_count = int(result.stdout.strip()) - 1  # Subtract header line
                
                if update_count > 50:
                    return {
                        'status': 'WARNING',
                        'message': f"Many updates available: {update_count}",
                        'recommendation': 'Schedule system updates'
                    }
                elif update_count > 0:
                    return {
                        'status': 'INFO',
                        'message': f"Updates available: {update_count}",
                        'recommendation': 'Consider updating system packages'
                    }
                else:
                    return {'status': 'PASS', 'message': 'System is up to date'}
            
            return {'status': 'INFO', 'message': 'Could not check for updates'}
            
        except Exception as e:
            return {'status': 'ERROR', 'message': f"Update check failed: {e}"}
    
    def _check_log_errors(self) -> Dict[str, Any]:
        """Check system logs for recent errors."""
        try:
            # Check recent system log errors
            result = self.ssh_client.execute_command(
                "journalctl --since='1 hour ago' --priority=err --no-pager | wc -l"
            )
            
            if result.success:
                error_count = int(result.stdout.strip())
                
                if error_count > 10:
                    return {
                        'status': 'WARNING',
                        'message': f"Many recent errors in logs: {error_count}",
                        'recommendation': 'Review system logs for issues'
                    }
                elif error_count > 0:
                    return {
                        'status': 'INFO',
                        'message': f"Some recent errors in logs: {error_count}",
                        'recommendation': 'Monitor log errors'
                    }
                else:
                    return {'status': 'PASS', 'message': 'No recent errors in logs'}
            
            return {'status': 'INFO', 'message': 'Could not check system logs'}
            
        except Exception as e:
            return {'status': 'ERROR', 'message': f"Log check failed: {e}"}
    
    def _check_file_permissions(self) -> Dict[str, Any]:
        """Check critical file permissions."""
        try:
            critical_files = [
                ('/etc/passwd', '644'),
                ('/etc/shadow', '640'),
                ('/etc/ssh/sshd_config', '644')
            ]
            
            permission_issues = []
            
            for file_path, expected_perms in critical_files:
                result = self.ssh_client.execute_command(f"stat -c %a {file_path}")
                if result.success:
                    actual_perms = result.stdout.strip()
                    if actual_perms != expected_perms:
                        permission_issues.append(f"{file_path}: {actual_perms} (expected {expected_perms})")
            
            if permission_issues:
                return {
                    'status': 'WARNING',
                    'message': f"File permission issues: {', '.join(permission_issues)}",
                    'recommendation': 'Review and fix file permissions'
                }
            else:
                return {'status': 'PASS', 'message': 'Critical file permissions are correct'}
                
        except Exception as e:
            return {'status': 'ERROR', 'message': f"Permission check failed: {e}"}

# Reporting and visualization
class MonitoringReporter:
    """
    Generate monitoring reports and visualizations.
    Demonstrates data analysis and reporting capabilities.
    """
    
    def __init__(self):
        self.report_templates = {
            'summary': self._generate_summary_report,
            'detailed': self._generate_detailed_report,
            'health': self._generate_health_report
        }
    
    def generate_report(self, report_type: str, data: Dict[str, Any], 
                       output_file: Optional[str] = None) -> str:
        """
        Generate monitoring report.
        
        Args:
            report_type: Type of report to generate
            data: Data for the report
            output_file: Optional output file path
            
        Returns:
            Report content as string
        """
        if report_type not in self.report_templates:
            raise ValueError(f"Unknown report type: {report_type}")
        
        report_content = self.report_templates[report_type](data)
        
        if output_file:
            with open(output_file, 'w') as f:
                f.write(report_content)
            logger.info(f"Report saved to {output_file}")
        
        return report_content
    
    def _generate_summary_report(self, data: Dict[str, Any]) -> str:
        """Generate summary monitoring report."""
        report = []
        report.append("=" * 60)
        report.append("SYSTEM MONITORING SUMMARY REPORT")
        report.append("=" * 60)
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        if 'metrics_summary' in data:
            summary = data['metrics_summary']
            report.append(f"Hostname: {summary.get('hostname', 'Unknown')}")
            report.append(f"Monitoring Period: {summary.get('period_hours', 0)} hours")
            report.append(f"Data Points: {summary.get('data_points', 0)}")
            report.append("")
            
            # CPU Usage
            if 'cpu_usage' in summary:
                cpu = summary['cpu_usage']
                report.append(f"CPU Usage:")
                report.append(f"  Average: {cpu['avg']:.1f}%")
                report.append(f"  Minimum: {cpu['min']:.1f}%")
                report.append(f"  Maximum: {cpu['max']:.1f}%")
                report.append("")
            
            # Memory Usage
            if 'memory_usage' in summary:
                memory = summary['memory_usage']
                report.append(f"Memory Usage:")
                report.append(f"  Average: {memory['avg']:.1f}%")
                report.append(f"  Minimum: {memory['min']:.1f}%")
                report.append(f"  Maximum: {memory['max']:.1f}%")
                report.append("")
            
            # Disk Usage
            if 'disk_usage' in summary:
                disk = summary['disk_usage']
                report.append(f"Disk Usage:")
                report.append(f"  Average: {disk['avg']:.1f}%")
                report.append(f"  Minimum: {disk['min']:.1f}%")
                report.append(f"  Maximum: {disk['max']:.1f}%")
                report.append("")
            
            report.append(f"Active Alerts: {summary.get('active_alerts', 0)}")
        
        return '\n'.join(report)
    
    def _generate_detailed_report(self, data: Dict[str, Any]) -> str:
        """Generate detailed monitoring report."""
        report = []
        report.append("=" * 60)
        report.append("DETAILED SYSTEM MONITORING REPORT")
        report.append("=" * 60)
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # Include all sections from summary report
        summary_content = self._generate_summary_report(data)
        report.append(summary_content)
        report.append("")
        
        # Add detailed sections
        if 'log_analysis' in data:
            report.append("LOG ANALYSIS")
            report.append("-" * 20)
            log_data = data['log_analysis']
            
            for category, count in log_data.get('summary', {}).items():
                report.append(f"{category.upper()}: {count} occurrences")
            
            if log_data.get('recent_errors'):
                report.append("\nRecent Errors:")
                for error in log_data['recent_errors'][:5]:
                    report.append(f"  - {error}")
            report.append("")
        
        if 'alerts' in data:
            report.append("ACTIVE ALERTS")
            report.append("-" * 20)
            for alert in data['alerts']:
                report.append(f"Alert: {alert.rule_name}")
                report.append(f"  Host: {alert.hostname}")
                report.append(f"  Metric: {alert.metric}")
                report.append(f"  Current Value: {alert.current_value}")
                report.append(f"  Threshold: {alert.threshold}")
                report.append(f"  Duration: {alert.duration}")
                report.append("")
        
        return '\n'.join(report)
    
    def _generate_health_report(self, data: Dict[str, Any]) -> str:
        """Generate health check report."""
        report = []
        report.append("=" * 60)
        report.append("SYSTEM HEALTH CHECK REPORT")
        report.append("=" * 60)
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        if 'health_check' in data:
            health_data = data['health_check']
            report.append(f"Hostname: {health_data.get('hostname', 'Unknown')}")
            report.append(f"Overall Status: {health_data.get('overall_status', 'UNKNOWN')}")
            report.append("")
            
            # Individual checks
            report.append("HEALTH CHECK RESULTS")
            report.append("-" * 30)
            for check_name, check_result in health_data.get('checks', {}).items():
                status = check_result.get('status', 'UNKNOWN')
                message = check_result.get('message', 'No message')
                report.append(f"{check_name.upper()}: {status}")
                report.append(f"  {message}")
                if 'recommendation' in check_result:
                    report.append(f"  Recommendation: {check_result['recommendation']}")
                report.append("")
            
            # Critical issues
            if health_data.get('critical_issues'):
                report.append("CRITICAL ISSUES")
                report.append("-" * 20)
                for issue in health_data['critical_issues']:
                    report.append(f"  - {issue}")
                report.append("")
            
            # Warnings
            if health_data.get('warnings'):
                report.append("WARNINGS")
                report.append("-" * 10)
                for warning in health_data['warnings']:
                    report.append(f"  - {warning}")
                report.append("")
            
            # Recommendations
            if health_data.get('recommendations'):
                report.append("RECOMMENDATIONS")
                report.append("-" * 15)
                for recommendation in health_data['recommendations']:
                    report.append(f"  - {recommendation}")
                report.append("")
        
        return '\n'.join(report)
    
    def create_metrics_chart(self, metrics_history: List[SystemMetrics], 
                           output_file: str = "metrics_chart.png"):
        """
        Create a chart of system metrics over time.
        
        Args:
            metrics_history: List of SystemMetrics objects
            output_file: Output file path for the chart
        """
        if not metrics_history:
            logger.warning("No metrics data available for charting")
            return
        
        # Prepare data
        timestamps = [m.timestamp for m in metrics_history]
        cpu_usage = [m.cpu_usage for m in metrics_history]
        memory_usage = [m.memory_usage for m in metrics_history]
        disk_usage = [m.disk_usage for m in metrics_history]
        
        # Create chart
        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10))
        fig.suptitle('System Metrics Over Time', fontsize=16)
        
        # CPU Usage
        ax1.plot(timestamps, cpu_usage, 'b-', label='CPU Usage')
        ax1.set_ylabel('CPU Usage (%)')
        ax1.set_ylim(0, 100)
        ax1.grid(True)
        ax1.legend()
        
        # Memory Usage
        ax2.plot(timestamps, memory_usage, 'g-', label='Memory Usage')
        ax2.set_ylabel('Memory Usage (%)')
        ax2.set_ylim(0, 100)
        ax2.grid(True)
        ax2.legend()
        
        # Disk Usage
        ax3.plot(timestamps, disk_usage, 'r-', label='Disk Usage')
        ax3.set_ylabel('Disk Usage (%)')
        ax3.set_ylim(0, 100)
        ax3.set_xlabel('Time')
        ax3.grid(True)
        ax3.legend()
        
        # Format x-axis
        for ax in [ax1, ax2, ax3]:
            ax.tick_params(axis='x', rotation=45)
        
        plt.tight_layout()
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        
        logger.info(f"Metrics chart saved to {output_file}")

# Database storage for metrics
class MetricsDatabase:
    """
    SQLite database for storing monitoring metrics.
    Demonstrates database operations and data persistence.
    """
    
    def __init__(self, db_path: str = "monitoring.db"):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialize database tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create metrics table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    hostname TEXT NOT NULL,
                    cpu_usage REAL,
                    memory_usage REAL,
                    disk_usage REAL,
                    load_avg_1 REAL,
                    load_avg_5 REAL,
                    load_avg_15 REAL,
                    process_count INTEGER,
                    uptime_seconds INTEGER,
                    network_bytes_received INTEGER,
                    network_bytes_sent INTEGER,
                    disk_reads INTEGER,
                    disk_writes INTEGER
                )
            ''')
            
            # Create alerts table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_name TEXT NOT NULL,
                    hostname TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    current_value REAL,
                    threshold REAL,
                    triggered_at TEXT NOT NULL,
                    resolved_at TEXT,
                    status TEXT DEFAULT 'ACTIVE'
                )
            ''')
            
            # Create health_checks table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS health_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    hostname TEXT NOT NULL,
                    check_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    recommendation TEXT
                )
            ''')
            
            conn.commit()
    
    def store_metrics(self, metrics: SystemMetrics):
        """Store system metrics in database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO metrics (
                    timestamp, hostname, cpu_usage, memory_usage, disk_usage,
                    load_avg_1, load_avg_5, load_avg_15, process_count, uptime_seconds,
                    network_bytes_received, network_bytes_sent, disk_reads, disk_writes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                metrics.timestamp.isoformat(),
                metrics.hostname,
                metrics.cpu_usage,
                metrics.memory_usage,
                metrics.disk_usage,
                metrics.load_average[0],
                metrics.load_average[1],
                metrics.load_average[2],
                metrics.process_count,
                metrics.uptime_seconds,
                metrics.network_io.get('bytes_received', 0),
                metrics.network_io.get('bytes_sent', 0),
                metrics.disk_io.get('reads', 0),
                metrics.disk_io.get('writes', 0)
            ))
            
            conn.commit()
    
    def store_alert(self, alert: Alert):
        """Store alert in database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO alerts (
                    rule_name, hostname, metric, current_value, threshold,
                    triggered_at, resolved_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                alert.rule_name,
                alert.hostname,
                alert.metric,
                alert.current_value,
                alert.threshold,
                alert.triggered_at.isoformat(),
                alert.resolved_at.isoformat() if alert.resolved_at else None,
                'RESOLVED' if alert.resolved_at else 'ACTIVE'
            ))
            
            conn.commit()
    
    def get_metrics_history(self, hostname: str, hours: int = 24) -> List[Dict[str, Any]]:
        """Get metrics history from database."""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM metrics 
                WHERE hostname = ? AND timestamp >= ?
                ORDER BY timestamp DESC
            ''', (hostname, cutoff_time.isoformat()))
            
            columns = [description[0] for description in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    def get_alert_history(self, hostname: str, days: int = 7) -> List[Dict[str, Any]]:
        """Get alert history from database."""
        cutoff_time = datetime.now() - timedelta(days=days)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM alerts 
                WHERE hostname = ? AND triggered_at >= ?
                ORDER BY triggered_at DESC
            ''', (hostname, cutoff_time.isoformat()))
            
            columns = [description[0] for description in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

# Email notification system
class EmailNotifier:
    """
    Email notification system for alerts and reports.
    Demonstrates email automation and notification systems.
    """
    
    def __init__(self, smtp_server: str, smtp_port: int, username: str, password: str):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
    
    def send_alert_notification(self, alert: Alert, recipients: List[str]):
        """Send alert notification email."""
        subject = f"ALERT: {alert.rule_name} on {alert.hostname}"
        
        body = f"""
System Alert Notification

Alert Details:
- Rule: {alert.rule_name}
- Hostname: {alert.hostname}
- Metric: {alert.metric}
- Current Value: {alert.current_value}
- Threshold: {alert.threshold}
- Triggered At: {alert.triggered_at}
- Duration: {alert.duration}

Please investigate this issue immediately.

This is an automated message from the system monitoring service.
        """
        
        self._send_email(recipients, subject, body)
    
    def send_health_report(self, health_data: Dict[str, Any], recipients: List[str]):
        """Send health check report email."""
        subject = f"Health Report: {health_data.get('hostname', 'Unknown')}"
        
        # Generate report content
        reporter = MonitoringReporter()
        report_content = reporter.generate_report('health', {'health_check': health_data})
        
        self._send_email(recipients, subject, report_content)
    
    def send_metrics_summary(self, metrics_summary: Dict[str, Any], recipients: List[str]):
        """Send metrics summary email."""
        subject = f"Metrics Summary: {metrics_summary.get('hostname', 'Unknown')}"
        
        # Generate report content
        reporter = MonitoringReporter()
        report_content = reporter.generate_report('summary', {'metrics_summary': metrics_summary})
        
        self._send_email(recipients, subject, report_content)
    
    def _send_email(self, recipients: List[str], subject: str, body: str):
        """Send email using SMTP."""
        try:
            msg = MimeMultipart()
            msg['From'] = self.username
            msg['To'] = ', '.join(recipients)
            msg['Subject'] = subject
            
            msg.attach(MimeText(body, 'plain'))
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)
            
            logger.info(f"Email sent to {recipients}: {subject}")
            
        except Exception as e:
            logger.error(f"Failed to send email: {e}")

# Comprehensive monitoring orchestrator
class MonitoringOrchestrator:
    """
    Main orchestrator for comprehensive system monitoring.
    Demonstrates system integration and workflow orchestration.
    """
    
    def __init__(self, ssh_client: AdvancedSSHClient, config: Dict[str, Any]):
        self.ssh_client = ssh_client
        self.config = config
        
        # Initialize components
        self.monitor = SystemMonitor(ssh_client)
        self.log_analyzer = LogAnalyzer(ssh_client)
        self.health_checker = HealthChecker(ssh_client)
        self.database = MetricsDatabase(config.get('database_path', 'monitoring.db'))
        self.reporter = MonitoringReporter()
        
        # Email notifier (optional)
        self.email_notifier = None
        if config.get('email_config'):
            email_config = config['email_config']
            self.email_notifier = EmailNotifier(
                email_config['smtp_server'],
                email_config['smtp_port'],
                email_config['username'],
                email_config['password']
            )
        
        # Scheduling
        self._setup_scheduled_tasks()
    
    def _setup_scheduled_tasks(self):
        """Setup scheduled monitoring tasks."""
        # Schedule health checks
        schedule.every(self.config.get('health_check_interval', 60)).minutes.do(
            self._run_health_check
        )
        
        # Schedule log analysis
        schedule.every(self.config.get('log_analysis_interval', 30)).minutes.do(
            self._run_log_analysis
        )
        
        # Schedule daily reports
        schedule.every().day.at(self.config.get('daily_report_time', "09:00")).do(
            self._send_daily_report
        )
        
        # Schedule weekly health reports
        schedule.every().monday.at(self.config.get('weekly_report_time', "09:00")).do(
            self._send_weekly_health_report
        )
    
    def start_monitoring(self):
        """Start comprehensive monitoring."""
        logger.info("Starting comprehensive system monitoring")
        
        # Start continuous metrics collection
        self.monitor.start_monitoring(
            interval_seconds=self.config.get('metrics_interval', 60)
        )
        
        # Start scheduled task runner
        self._start_scheduler()
        
        logger.info("Monitoring system started successfully")
    
    def stop_monitoring(self):
        """Stop monitoring."""
        logger.info("Stopping system monitoring")
        self.monitor.stop_monitoring()
        logger.info("Monitoring system stopped")
    
    def _start_scheduler(self):
        """Start the task scheduler in a background thread."""
        def scheduler_loop():
            while self.monitor.monitoring_active:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
        
        scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
        scheduler_thread.start()
    
    def _run_health_check(self):
        """Run scheduled health check."""
        try:
            logger.info("Running scheduled health check")
            health_results = self.health_checker.run_health_check()
            
            # Store results in database
            for check_name, check_result in health_results.get('checks', {}).items():
                # Store in database (implementation depends on your schema)
                pass
            
            # Send notifications for critical issues
            if health_results.get('critical_issues') and self.email_notifier:
                recipients = self.config.get('alert_recipients', [])
                if recipients:
                    self.email_notifier.send_health_report(health_results, recipients)
            
        except Exception as e:
            logger.error(f"Scheduled health check failed: {e}")
    
    def _run_log_analysis(self):
        """Run scheduled log analysis."""
        try:
            logger.info("Running scheduled log analysis")
            
            log_files = self.config.get('log_files', ['/var/log/syslog'])
            
            for log_file in log_files:
                analysis_results = self.log_analyzer.analyze_log_file(log_file)
                
                # Check for concerning patterns
                error_count = analysis_results.get('summary', {}).get('error', 0)
                security_count = analysis_results.get('summary', {}).get('security', 0)
                
                # Send alerts if thresholds exceeded
                if (error_count > self.config.get('error_threshold', 10) or 
                    security_count > self.config.get('security_threshold', 5)):
                    
                    if self.email_notifier:
                        recipients = self.config.get('alert_recipients', [])
                        if recipients:
                            subject = f"Log Analysis Alert: {log_file}"
                            body = f"High error/security event count detected:\n"
                            body += f"Errors: {error_count}\n"
                            body += f"Security events: {security_count}\n"
                            body += f"Log file: {log_file}"
                            
                            self.email_notifier._send_email(recipients, subject, body)
            
        except Exception as e:
            logger.error(f"Scheduled log analysis failed: {e}")
    
    def _send_daily_report(self):
        """Send daily monitoring report."""
        try:
            logger.info("Generating daily monitoring report")
            
            # Get metrics summary
            metrics_summary = self.monitor.get_metrics_summary(hours=24)
            
            # Generate report
            report_data = {
                'metrics_summary': metrics_summary,
                'alerts': list(self.monitor.active_alerts.values())
            }
            
            report_content = self.reporter.generate_report('detailed', report_data)
            
            # Save report to file
            report_file = f"daily_report_{datetime.now().strftime('%Y%m%d')}.txt"
            with open(report_file, 'w') as f:
                f.write(report_content)
            
            # Send email report
            if self.email_notifier:
                recipients = self.config.get('report_recipients', [])
                if recipients:
                    self.email_notifier.send_metrics_summary(metrics_summary, recipients)
            
        except Exception as e:
            logger.error(f"Daily report generation failed: {e}")
    
    def _send_weekly_health_report(self):
        """Send weekly health report."""
        try:
            logger.info("Generating weekly health report")
            
            # Run comprehensive health check
            health_results = self.health_checker.run_health_check()
            
            # Generate report
            report_content = self.reporter.generate_report('health', {'health_check': health_results})
            
            # Save report to file
            report_file = f"weekly_health_report_{datetime.now().strftime('%Y%m%d')}.txt"
            with open(report_file, 'w') as f:
                f.write(report_content)
            
            # Send email report
            if self.email_notifier:
                recipients = self.config.get('report_recipients', [])
                if recipients:
                    self.email_notifier.send_health_report(health_results, recipients)
            
        except Exception as e:
            logger.error(f"Weekly health report generation failed: {e}")

# Demonstration and example usage
def demonstrate_system_monitoring():
    """Demonstrate system monitoring capabilities."""
    print("=== System Monitoring Demo ===")
    
    # Example configuration
    config = ServerConfig(
        hostname="example.com",
        username="user",
        password="password"
    )
    
    try:
        with AdvancedSSHClient(config) as ssh:
            # Initialize monitor
            monitor = SystemMonitor(ssh)
            
            # Collect single metrics sample
            metrics = monitor.collect_metrics()
            if metrics:
                print(f"Current metrics for {metrics.hostname}:")
                print(f"  CPU Usage: {metrics.cpu_usage:.1f}%")
                print(f"  Memory Usage: {metrics.memory_usage:.1f}%")
                print(f"  Disk Usage: {metrics.disk_usage:.1f}%")
                print(f"  Load Average: {metrics.load_average}")
                print(f"  Process Count: {metrics.process_count}")
                print(f"  Uptime: {metrics.uptime_seconds} seconds")
            
            # Check alerts
            alerts = monitor.check_alerts(metrics)
            if alerts:
                print(f"\nTriggered alerts: {len(alerts)}")
                for alert in alerts:
                    print(f"  - {alert.rule_name}: {alert.current_value} > {alert.threshold}")
            else:
                print("\nNo alerts triggered")
    
    except Exception as e:
        logger.error(f"Monitoring demo failed: {e}")

def demonstrate_log_analysis():
    """Demonstrate log analysis capabilities."""
    print("=== Log Analysis Demo ===")
    
    config = ServerConfig(
        hostname="example.com",
        username="user",
        password="password"
    )
    
    try:
        with AdvancedSSHClient(config) as ssh:
            analyzer = LogAnalyzer(ssh)
            
            # Analyze system log
            results = analyzer.analyze_log_file("/var/log/syslog", lines=500)
            
            if results:
                print(f"Log analysis results for {results['file_path']}:")
                print(f"  Lines analyzed: {results['lines_analyzed']}")
                
                for category, count in results['summary'].items():
                    print(f"  {category.upper()}: {count} occurrences")
                
                if results['recent_errors']:
                    print("\nRecent errors:")
                    for error in results['recent_errors'][:3]:
                        print(f"  - {error}")
            
            # Search for specific patterns
            patterns = ['failed', 'error', 'warning']
            search_results = analyzer.search_log_patterns("/var/log/syslog", patterns)
            
            for pattern, matches in search_results.items():
                print(f"\nPattern '{pattern}': {len(matches)} matches")
    
    except Exception as e:
        logger.error(f"Log analysis demo failed: {e}")

def demonstrate_health_check():
    """Demonstrate health check capabilities."""
    print("=== Health Check Demo ===")
    
    config = ServerConfig(
        hostname="example.com",
        username="user",
        password="password"
    )
    
    try:
        with AdvancedSSHClient(config) as ssh:
            health_checker = HealthChecker(ssh)
            
            # Run comprehensive health check
            results = health_checker.run_health_check()
            
            print(f"Health check results for {results['hostname']}:")
            print(f"Overall Status: {results['overall_status']}")
            print()
            
            # Show individual check results
            for check_name, check_result in results['checks'].items():
                status = check_result['status']
                message = check_result['message']
                print(f"{check_name}: {status}")
                print(f"  {message}")
                
                if 'recommendation' in check_result:
                    print(f"  Recommendation: {check_result['recommendation']}")
                print()
            
            # Show critical issues
            if results['critical_issues']:
                print("CRITICAL ISSUES:")
                for issue in results['critical_issues']:
                    print(f"  - {issue}")
            
            # Show warnings
            if results['warnings']:
                print("WARNINGS:")
                for warning in results['warnings']:
                    print(f"  - {warning}")
    
    except Exception as e:
        logger.error(f"Health check demo failed: {e}")

def demonstrate_comprehensive_monitoring():
    """Demonstrate comprehensive monitoring orchestration."""
    print("=== Comprehensive Monitoring Demo ===")
    
    # Configuration
    ssh_config = ServerConfig(
        hostname="example.com",
        username="user",
        password="password"
    )
    
    monitoring_config = {
        'database_path': 'demo_monitoring.db',
        'metrics_interval': 30,
        'health_check_interval': 60,
        'log_analysis_interval': 30,
        'daily_report_time': "09:00",
        'weekly_report_time': "09:00",
        'log_files': ['/var/log/syslog', '/var/log/auth.log'],
        'error_threshold': 10,
        'security_threshold': 5,
        'alert_recipients': ['admin@example.com'],
        'report_recipients': ['admin@example.com']
    }
    
    try:
        with AdvancedSSHClient(ssh_config) as ssh:
            # Initialize orchestrator
            orchestrator = MonitoringOrchestrator(ssh, monitoring_config)
            
            print("Starting comprehensive monitoring...")
            print("This would normally run continuously.")
            print("For demo purposes, we'll run a few cycles.")
            
            # Start monitoring (in real usage, this would run indefinitely)
            orchestrator.start_monitoring()
            
            # Let it run for a short time
            time.sleep(120)  # 2 minutes
            
            # Stop monitoring
            orchestrator.stop_monitoring()
            
            print("Monitoring demo completed.")
    
    except Exception as e:
        logger.error(f"Comprehensive monitoring demo failed: {e}")

def main():
    """Main demonstration function."""
    print("=== Unix System Monitoring Examples ===")
    
    print("\n1. System Monitoring Demo")
    print("   (Requires server credentials - modify demonstrate_system_monitoring())")
    # demonstrate_system_monitoring()
    
    print("\n2. Log Analysis Demo")
    print("   (Requires server credentials - modify demonstrate_log_analysis())")
    # demonstrate_log_analysis()
    
    print("\n3. Health Check Demo")
    print("   (Requires server credentials - modify demonstrate_health_check())")
    # demonstrate_health_check()
    
    print("\n4. Comprehensive Monitoring Demo")
    print("   (Requires server credentials - modify demonstrate_comprehensive_monitoring())")
    # demonstrate_comprehensive_monitoring()
    
    print("\nTo use these examples:")
    print("1. Install dependencies: pip install paramiko scp matplotlib pandas schedule")
    print("2. Modify server credentials in the demo functions")
    print("3. Configure email settings for notifications")
    print("4. Run the appropriate demonstration function")
    
    print("\nMonitoring features demonstrated:")
    print("- Real-time system metrics collection")
    print("- Automated alerting and notifications")
    print("- Log file analysis and pattern detection")
    print("- Comprehensive health checking")
    print("- Database storage and historical analysis")
    print("- Report generation and visualization")
    print("- Email notifications and reporting")
    print("- Scheduled monitoring tasks")

if __name__ == "__main__":
    main()
