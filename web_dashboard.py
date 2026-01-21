"""
Simple Web Dashboard for Trading System
Run this locally to view system status from your mobile browser
"""

from flask import Flask, render_template, jsonify, request
import json
import os
import psutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

app = Flask(__name__)

# Store the process reference (in production, use a proper process manager)
main_process = None

def load_positions():
    """Load active positions from JSON file"""
    try:
        with open('active_positions.json', 'r') as f:
            positions = json.load(f)
        return [p for p in positions if p.get('status') == 'OPEN']
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []

def get_today_log_file():
    """Get path to today's log file"""
    today = datetime.now().strftime('%Y%m%d')
    log_file = f'logs/trading_system_{today}.log'
    if os.path.exists(log_file):
        return log_file
    return None

def get_recent_logs(n=50):
    """Get recent log entries"""
    log_file = get_today_log_file()
    if not log_file:
        return []
    
    try:
        with open(log_file, 'r') as f:
            lines = f.readlines()
            return lines[-n:] if len(lines) > n else lines
    except:
        return []

def get_last_log_timestamp():
    """Get timestamp of last log entry"""
    log_file = get_today_log_file()
    if not log_file:
        return None
    
    try:
        # Get last line and extract timestamp
        with open(log_file, 'r') as f:
            lines = f.readlines()
            if not lines:
                return None
            
            last_line = lines[-1].strip()
            # Try to parse timestamp from log format: "2026-01-21 15:30:00,123 - ..."
            if last_line and len(last_line) > 19:
                try:
                    timestamp_str = last_line[:19]  # "2026-01-21 15:30:00"
                    return datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                except:
                    return None
    except:
        return None
    
    return None

def is_main_process_running():
    """Check if main.py process is running"""
    global main_process
    
    # Check stored process first
    if main_process and main_process.poll() is None:
        return True
    
    # Check all processes
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', [])
                if cmdline and 'main.py' in ' '.join(cmdline):
                    # Make sure it's not the dashboard itself
                    if 'web_dashboard.py' not in ' '.join(cmdline):
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except:
        pass
    return False

def get_main_process_pid():
    """Get the PID of the main.py process"""
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', [])
                if cmdline and 'main.py' in ' '.join(cmdline):
                    if 'web_dashboard.py' not in ' '.join(cmdline):
                        return proc.info['pid']
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except:
        pass
    return None

def can_start_system():
    """Check if system can be started (after 9:15 AM, weekday)"""
    now = datetime.now()
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    # Check if it's a weekday
    is_weekday = now.weekday() < 5
    
    # Can start if: it's a weekday AND (it's after 9:15 AM AND before 3:30 PM)
    can_start = is_weekday and (now >= market_open and now < market_close)
    
    return can_start, {
        'is_weekday': is_weekday,
        'current_time': now.strftime('%H:%M:%S'),
        'market_open': market_open.strftime('%H:%M:%S'),
        'market_close': market_close.strftime('%H:%M:%S'),
        'after_market_open': now >= market_open,
        'before_market_close': now < market_close
    }

def is_market_hours():
    """Check if current time is during market hours (9:15 AM - 3:30 PM IST)"""
    now = datetime.now()
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    # Check if it's a weekday (Monday=0, Sunday=6)
    is_weekday = now.weekday() < 5
    
    return is_weekday and market_open <= now <= market_close

def get_system_status():
    """Get system status information"""
    now = datetime.now()
    process_running = is_main_process_running()
    last_log_time = get_last_log_timestamp()
    market_open = is_market_hours()
    
    # Determine if system is active
    # System is active if:
    # 1. Process is running, OR
    # 2. Last log entry is within last 2 minutes AND it's market hours
    is_active = False
    if process_running:
        is_active = True
    elif last_log_time:
        time_since_last_log = (now - last_log_time).total_seconds()
        # If last log was within 2 minutes and it's market hours, consider active
        if time_since_last_log < 120 and market_open:
            is_active = True
    
    status = {
        'positions_file_exists': os.path.exists('active_positions.json'),
        'log_file_exists': get_today_log_file() is not None,
        'log_file': get_today_log_file(),
        'timestamp': now.isoformat(),
        'is_active': is_active,
        'process_running': process_running,
        'last_log_time': last_log_time.isoformat() if last_log_time else None,
        'market_hours': market_open,
        'time_since_last_log': (now - last_log_time).total_seconds() if last_log_time else None
    }
    return status

@app.route('/')
def dashboard():
    """Main dashboard page"""
    positions = load_positions()
    status = get_system_status()
    return render_template('dashboard.html', positions=positions, status=status)

@app.route('/api/positions')
def api_positions():
    """API endpoint for positions"""
    positions = load_positions()
    return jsonify(positions)

@app.route('/api/status')
def api_status():
    """API endpoint for system status"""
    status = get_system_status()
    positions = load_positions()
    status['open_positions_count'] = len(positions)
    return jsonify(status)

@app.route('/api/logs')
def api_logs():
    """API endpoint for recent logs"""
    logs = get_recent_logs(100)
    return jsonify({'logs': logs})

@app.route('/api/start', methods=['POST'])
def api_start():
    """API endpoint to start the trading system"""
    global main_process
    
    # Check if already running
    if is_main_process_running():
        return jsonify({
            'success': False,
            'message': 'Trading system is already running'
        }), 400
    
    # Check if we can start (after 9:15 AM, weekday)
    can_start, details = can_start_system()
    if not can_start:
        reason = []
        if not details['is_weekday']:
            reason.append('Not a weekday')
        if not details['after_market_open']:
            reason.append(f"Before market open (9:15 AM). Current time: {details['current_time']}")
        if not details['before_market_close']:
            reason.append(f"After market close (3:30 PM). Current time: {details['current_time']}")
        
        return jsonify({
            'success': False,
            'message': 'Cannot start system: ' + ', '.join(reason),
            'details': details
        }), 400
    
    try:
        # Get the directory where this script is located
        script_dir = os.path.dirname(os.path.abspath(__file__))
        main_py_path = os.path.join(script_dir, 'main.py')
        
        # Determine Python executable
        python_exe = sys.executable
        
        # Start main.py as a subprocess
        # Use nohup-like behavior: detach from parent, redirect output
        main_process = subprocess.Popen(
            [python_exe, main_py_path],
            cwd=script_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True  # Detach from parent process
        )
        
        return jsonify({
            'success': True,
            'message': 'Trading system started successfully',
            'pid': main_process.pid
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Failed to start system: {str(e)}'
        }), 500

@app.route('/api/stop', methods=['POST'])
def api_stop():
    """API endpoint to stop the trading system"""
    global main_process
    
    if not is_main_process_running():
        return jsonify({
            'success': False,
            'message': 'Trading system is not running'
        }), 400
    
    try:
        # Try to stop the stored process first
        if main_process and main_process.poll() is None:
            main_process.terminate()
            # Wait a bit for graceful shutdown
            try:
                main_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                main_process.kill()
            main_process = None
            return jsonify({
                'success': True,
                'message': 'Trading system stopped successfully'
            })
        
        # If stored process doesn't work, find and kill by PID
        pid = get_main_process_pid()
        if pid:
            try:
                proc = psutil.Process(pid)
                proc.terminate()
                # Wait a bit for graceful shutdown
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    proc.kill()
                return jsonify({
                    'success': True,
                    'message': 'Trading system stopped successfully'
                })
            except psutil.NoSuchProcess:
                return jsonify({
                    'success': False,
                    'message': 'Process not found'
                }), 404
            except Exception as e:
                return jsonify({
                    'success': False,
                    'message': f'Failed to stop process: {str(e)}'
                }), 500
        
        return jsonify({
            'success': False,
            'message': 'Could not find running process'
        }), 404
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Failed to stop system: {str(e)}'
        }), 500

if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)
    
    print("=" * 50)
    print("Trading System Web Dashboard")
    print("=" * 50)
    print("Starting server...")
    print("Access from your mobile:")
    print("  http://<your-computer-ip>:5001")
    print("  or")
    print("  http://localhost:5001 (on same device)")
    print("=" * 50)
    
    # Run on all interfaces so mobile can access
    # Using port 5001 to avoid conflict with macOS AirPlay Receiver on port 5000
    app.run(host='0.0.0.0', port=5001, debug=True)
