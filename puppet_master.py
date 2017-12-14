import os
import time
import requests

from subprocess import call, check_call
from contextlib import closing
from threading import Thread

from poppyd import PoppyDaemon
from config import Config, attrsetter


class PuppetMaster(object):
    def __init__(self, DaemonCls, configfile, pidfile):
        self.configfile = os.path.abspath(configfile)
        self.pidfile = os.path.abspath(pidfile)
        self.logfile = self.config.info.logfile

        self.daemon = DaemonCls(self.configfile, self.pidfile)

        self.config_handlers = {
            'robot.camera': lambda _: self.restart(),
            'robot.name': self._change_hostname,
        }
        self._updating = False

    def start(self):
        # Kill all robot instances in Jupyter that could interfere with poppy-services
        self._stop_jupyter_kernels("http://localhost:8888")
        self.daemon.start()

    @property
    def running(self):
        return 'running' in self.daemon.status()

    def stop(self):
        try:
            self.daemon.stop()
        except (OSError, SystemError):
            self.force_clean()

    def restart(self):
        if self.running:
            self.stop()

        self.start()

    def force_clean(self):
        self.daemon.force_clean()
        call(['pkill', '-f', 'poppy-services'])

    @property
    def config(self):
        return Config.from_file(self.configfile)

    def update_config(self, key, value):
        with closing(self.config) as c:
            attrsetter(key)(c, value)

        if key in self.config_handlers:
            self.config_handlers[key](value)

    def self_update(self):
        if self._updating:
            return

        self._updating = True
        self.stop()

        if os.path.exists(self.config.update.logfile):
            os.remove(self.config.update.logfile)
        success = check_call(['poppy-update'])

        self.start()
        self._updating = False

        return success

    @property
    def is_updating(self):
        return self._updating

    def _change_hostname(self, name):
        call(['sudo', 'raspi-config', '--change-hostname', name])
        call(['sudo', 'hostnamectl', 'set-hostname', name])
        call(['sudo', 'systemctl', 'restart', 'networking.service'])
        call(['sudo', 'systemctl', 'restart', 'avahi-daemon.service'])
        self.restart()

    def shutdown(self):
        try:
            for m in self.get_motors():
                self.send_value(m, 'compliant', True)
        except:
            pass

        def delayed_halt(sec=5):
            time.sleep(sec)
            call(['sudo', 'halt'])
        Thread(target=delayed_halt).start()

    def get_motors(self):
        r = requests.get('http://localhost:8080/motor/list.json').json()
        return r['motors']

    def send_value(self, motor, register, value):
        url = 'http://localhost:8080/motor/{}/register/{}/value.json'
        r = requests.post(url.format(motor, register), json=value)
        return r

    def _stop_jupyter_kernels(self, base_url):
        """
        Shutdown Jupyter notebook kernels using Jupyter REST API
        """
        # A GET request on main page is needed to have the xsrf token in cookies
        client = requests.session()
        client.get(base_url)
        kernels = client.get("%s/api/kernels" % base_url).json()
        for k in kernels:
            client.delete("%s/api/kernels/%s" % (base_url, k["id"]),
                          data = {"_xsrf": client.cookies['_xsrf']})

if __name__ == '__main__':
    import sys

    configfile = os.path.expanduser('~/.poppy_config.yaml')
    pidfile = '/tmp/puppet-master-pid.lock'

    puppet_master = PuppetMaster(DaemonCls=PoppyDaemon,
                                 configfile=configfile,
                                 pidfile=pidfile)

    if sys.argv[1] == 'start':
        puppet_master.start()
    elif sys.argv[1] == 'stop':
        puppet_master.stop()
