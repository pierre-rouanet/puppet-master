import os
import sys
import requests
import argparse
import subprocess

from threading import Thread

from flask import (Flask, request, Markup,
                   redirect, url_for,
                   render_template, flash,
                   send_from_directory, Response,
                   copy_current_request_context)

from pypot.creatures import installed_poppy_creatures

from poppyd import PoppyDaemon

if sys.version_info < (3, 3):
    from urlparse import urlparse
else:
    from urllib.parse import urlparse


parser = argparse.ArgumentParser(description='Serve the webinterface '
                                             'for controlling Poppy robots')
parser.add_argument('--debug', action='store_true',
                    help='use the debug mode')
parser.add_argument('--test', action='store_true',
                    help='does not modify anything on your machine '
                         '(except from a config file in /tmp)')
parser.add_argument('--creature', choices=installed_poppy_creatures.keys(),
                    help='Which creature to use (by default will use the one set in the yaml config).')
args = parser.parse_args()


if args.test:
    from dummy_pm import PuppetMaster
else:
    from puppet_master import PuppetMaster


app = Flask(__name__)
app.secret_key = os.urandom(24)

if args.debug:
    app.debug = True

if args.test:
    if not args.creature:
        print('You must choose a creature in test mode!\n')
        parser.print_help()
        sys.exit(1)

    configfile = '/tmp/poppy_config.yaml'
    subprocess.call(['python', 'bootstrap.py',
                     '--config-path', configfile,
                     'localhost', args.creature])
else:
    configfile = os.path.expanduser('~/.poppy_config.yaml')

pidfile = '/tmp/puppet-master-pid.lock'

pm = PuppetMaster(DaemonCls=PoppyDaemon,
                  configfile=configfile,
                  pidfile=pidfile)

if os.path.exists(pidfile):
    pm.force_clean()

if pm.config.robot.autoStart:
    pm.start()
else:
    with open(pm.config.info.logfile, 'w') as log:
        log.write('Auto-start API disable ! \nShow configuration page for enable auto-start, or start manually.')
        log.close()

number=int(pm.config.robot.virtualBot)
if number>0:
    pm.clone(number)


@app.context_processor
def inject_robot_config():
    return dict(robot=pm.config.robot,
                info=pm.config.info,
                wifi=pm.config.wifi,
                hotspot=pm.config.hotspot,
                clone=pm.nb_clone)

@app.after_request
def cache_buster(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/')
def index():
    if pm.config.robot.firstPage:
        return render_template('opening.html')
    else:
        return render_template('index.html')

@app.route('/end_opening')
def end_opening():
    pm.update_config('robot.firstPage', False)
    return render_template('index.html')


@app.route('/monitor')
def monitor():
    if not pm.running:
        flash(Markup('> API is <b>not running</b>, start before use Monitor. &nbsp; > Show <a href="{}">logs</a> or <a onclick="refreshForMsg(\'{}\')">Start</a> now'.format(url_for('logs'),url_for('APIstart'))), 'alert')

    return render_template(
        'base-iframe.html',
        iframe_src=url_for(
            'base_static_monitor',
            filename='{}.html'.format(pm.config.robot.creature)
        )
    )


@app.route('/monitor/<path:filename>')
def base_static_monitor(filename):
    return send_from_directory(app.root_path + '/monitor/', filename)


@app.route('/snap')
def snap():
    if not pm.running:
        flash(Markup('> API is <b>not running</b>, start before use Snap. &nbsp; > Show <a href="{}">logs</a> or <a onclick="refreshForMsg(\'{}\')">Start</a> now'.format(url_for('logs'),url_for('APIstart'))), 'alert')

    return render_template(
        'base-iframe.html',
        iframe_src=url_for('base_static_snap', filename='snap.html')
    )


@app.route('/snap/<path:filename>')
def base_static_snap(filename):
    return send_from_directory(app.root_path + '/snap/', filename)


@app.route('/jupyter')
def jupyter():
    if pm.running:
        flash(Markup('> API is <b>already running</b>, stop before instanciate the robot on python. &nbsp; > Show <a href="{}">logs</a> or <a onclick="refreshForMsg(\'{}\')">Stop</a> now'.format(url_for('logs'),url_for('APIstop'))), 'alert')

    return render_template(
        'base-iframe.html',
        iframe_src='http://{}:8888'.format(urlparse(request.url_root).hostname)
    )


@app.route('/settings')
def settings():
    return render_template('settings.html')


@app.route('/settings_update', methods=['POST'])
def settings_update():
    label= {'name': 'Hostname',
            'firstPage':'State for first connection page',
            'autoStart':'API auto-start',
            'camera':'State of camera (when API start)',
            'virtualBot':'Number of virtual instance',
            'start':'State',
            'ssid':'Name',
            'psk':'Password'}
    msg=''
    for key, value in request.form.items() :
        if value != '':
            key=key.split('_')
            value=value.replace(' ','')#prevent user mistake
            if value == 'on': value = True
            elif value == 'off': value = False
            if value != getattr(getattr(pm.config, key[0]), key[1]):
                pm.update_config('.'.join(key),value)
                msg+='> {} of {} was changed.'.format(label[key[1]], key[0])
                if key[1] == 'name' or key[0] == 'hotspot' or key[0] == 'wifi':
                    msg+=' <a href="{}">Restart network service</a> to apply (or wait next reboot).'.format(url_for('restart_network'))
                msg+='<br>'
    if msg == '': flash('> Nothing was changed!', 'warning')
    else: flash(Markup(msg), 'success')
    return ('', 204)

@app.route('/restart_network')
def restart_network():
    pm.restart_network()
    goback= request.referrer.replace(urlparse(request.url_root).hostname, pm.config.robot.name+'.local')
    flash('> Network service was restarted', 'success')
    return redirect(goback)

@app.route('/terminal')
def terminal():
    if pm.running:
        pm.stop()
    return render_template(
        'base-iframe.html',
        iframe_src='http://{}:8888/terminals/poppy'.format(urlparse(request.url_root).hostname)
    )

@app.route('/reboot')
def reboot():
    pm.reboot()
    flash('> Your {} will now be REBOOT in a few seconds.'.format(pm.config.info.board), 'success')
    return ('', 204)

@app.route('/logs')
def logs():
    try:
        with open(pm.config.info.logfile) as f:
            content = f.read()
    except IOError:
        content = 'No log found...'
    return render_template('logs.html', logs_content=content)


@app.route('/update-logs')
def update_logs():
    try:
        with open(pm.config.update.logfile) as f:
            content = f.read()
    except IOError:
        content = 'No log found...'
    return render_template('update.html', update_logs_content=content)


@app.route('/APIreset')
def APIreset():
    if pm.running:
        pm.stop()
    pm.start()
    flash('> Your robot\'s API has been restarted.', 'success')
    return ('', 204)

@app.route('/APIstart')
def APIstart():
    if pm.running:
        flash('> Your robot\'s API has already started.', 'warning')
    else:
        pm.start()
        flash('> Your robot\'s API has been started.', 'success')
    return ('', 204)

@app.route('/APIstop')
def APIstop():
    if pm.running:
        pm.stop()
        flash('> Your robot\'s API has been stopped.', 'success')
    else:
        flash('> Your robot\'s API has already stopped.', 'warning')
    return ('', 204)


@app.route('/update')
def update():
    @copy_current_request_context
    def update_in_bg():
        success = pm.self_update()

        if success:
            flash('> Your robot is now up-to-date!', 'success')
        else:
            flash('> Update failed, check logs for details!', 'alert')
        #flash msg probably does not work, check this after fixing updating bug

    flash('> Your robot is currently updating. Please do not turn it off before it\'s done!', 'warning')

    if not pm.is_updating:
        Thread(target=update_in_bg).start()

    return redirect(url_for('update_logs'))


@app.route('/updating')
def is_updating():
    return 'true' if pm.is_updating else 'false'


@app.route('/done-updating')
def done_updating():
    flash('> Your robot is now up-to-date!', 'success')
    return redirect(url_for('index'))

@app.route('/switch_camera')
def switch_camera():
    if pm.config.robot.camera:
        pm.update_config('robot.camera', False)
        flash('> Your robot camera is now turned off!', 'success')
    else:
        pm.update_config('robot.camera', True)
        flash('> Your robot camera is now turned on!', 'success')
    return ('', 204)

@app.route('/clone')
def clone():
    pm.clone()
    flash('> One more instance was launched', 'success')
    return ('', 204)

@app.route('/configure-motors')
def configure_motors():
    # Remove old poppy-configure output to avoid user confusion
    try:
        os.remove(pm.config.info.configMotorLog)
    except OSError:
        pass
    return render_template('motor-configuration.html', motors=pm._get_robot_motor_list())


@app.route('/call_poppy_configure', methods=['POST'])
def call_poppy_configure():
    motor = request.form['motor']
    pm.update_config('robot.motors', motor)
    return ('', 204)


@app.route('/ready-to-roll')
def ready_to_roll():
    with open(pm.config.info.logfile) as f:
        content = f.read()

    if 'SnapRobotServer is now running on' not in content:
        return 'KO'

    try:
        r = requests.get('http://{}:6969/motors/get/positions'.format(get_host()))
        if r.status_code == 200:
            return 'OK'
    except:
        pass

    return 'KO'


@app.route('/shutdown')
def shutdown():
    pm.shutdown()
    flash('> Your {} will now be HALTED in a few seconds.'.format(pm.config.info.board), 'success')
    return ('', 204)


@app.route('/api/raw_logs')
def raw_logs():
    try:
        with open(pm.config.info.logfile) as f:
            content = f.read()
    except IOError:
        content = 'No log found...'
    return Response(content, mimetype='text/plain')

@app.route('/api/raw_logs_vir', methods=['POST'])
def raw_logs_vir():
    file= pm.config.info.virtualBotLog.replace('.log', '_{}.log'.format(request.form['id']))
    try:
        with open(file) as f:
            content = f.read()
    except IOError:
        content = 'No log found...'
    return Response(content, mimetype='text/plain')

@app.route('/api/update_raw_logs')
def update_raw_logs():
    try:
        with open(pm.config.update.logfile) as f:
            content = f.read()
    except IOError:
        content = 'No log found...'
    return Response(content, mimetype='text/plain')


@app.route('/api/configure_motors_logs')
def poppy_config_logs():
    try:
        with open(pm.config.info.configMotorLog) as f:
            content = f.read()
    except IOError:
        content = ''
    return Response(content, mimetype='text/plain')


def get_host():
    host = pm.config.robot.name
    host = host if host == 'localhost' else '{}.local'.format(host)
    return host


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=2280)
