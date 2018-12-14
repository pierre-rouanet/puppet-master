import os
import time
import re
import requests

from subprocess import call, check_call
from contextlib import closing
from threading import Thread
from string import Template

from poppyd import PoppyDaemon
from config import Config, attrsetter
from pypot.creatures import installed_poppy_creatures


class PuppetMaster(object):
    def __init__(self, DaemonCls, configfile, pidfile):
        self.configfile = os.path.abspath(configfile)
        self.pidfile = os.path.abspath(pidfile)
        self.logfile = self.config.info.logfile

        self.daemon = DaemonCls(self.configfile, self.pidfile)

        self.config_handlers = {
            'robot.camera': lambda _: self.restart(),
            'robot.name': self._change_hostname,
            'robot.motors': self._configure_motors
        }
        self._updating = False

    def start(self):
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

    def _get_robot_motor_list(self):
        try:
            RobotCls = installed_poppy_creatures[self.config.robot.creature]
            return sorted(RobotCls.default_config['motors'].keys())
        except KeyError:
            return ['']

    def _configure_motors(self, motor):
        self.stop()
        creature = self.config.robot.creature.split('poppy-')[1]
        f = open(self.config.poppy_configure.logfile,"wb")
        check_call(['poppy-configure', creature, motor], stdout=f, stderr=f)
        self.start()

    def _set_hostspot_configuration(self, ssid, passphrase, hide_hostname=0):
        conf = "ssid=%s\npassphrase=%s\nhide_hostname=%s\n" % (ssid, passphrase, hide_hostname)
        with open('/tmp/hotspot.conf', 'w') as f:
            f.write(conf)

        os.system('sudo mv /tmp/hotspot.conf %s' % self.config.wifi.hostspot_conf_file)
        call(['sudo', 'systemctl', 'restart', self.config.wifi.hostspot_service])

    def _get_hostspot_configuration(self):
        conf=''
        with open(self.config.wifi.hostspot_conf_file, 'r') as f:
            conf = f.read()
        ssid=re.match('s/^ssid=\(.\+\)$/\1/p', conf)
        passphrase = re.match('s/^passphrase=\(.\+\)$/\1/p', conf)
        hide_hostname = re.match('s/^hide_hostname=\(.\+\)$/\1/p', conf)
        return {'ssid'=ssid, 'passphrase'=passphrase, 'hide_hostname'=hide_hostname}

    def _disable_hostpot(self):
        conf_path = self.config.wifi.hostspot_conf_file
        if os.path.isfile(conf_path):
            os.system("sudo mv %s %s" % (conf_path, os.path.join(conf_path, '.backup'))
            call(['sudo', 'systemctl', 'restart', self.config.wifi.hostspot_service])

    def _enable_hostspot(self):
        conf_path = self.config.wifi.hostspot_conf_file
        if os.path.isfile(os.path.join(conf_path, '.backup')):
            cmd = "sudo mv %s %s" % (os.path.join(conf_path, '.backup'), conf_path)
            call(['sudo', 'systemctl', 'restart', self.config.wifi.hostspot_service])

        else:
            self._set_hostspot_configuration(self.config.wifi.default_ssid, self.config.wifi.default_password)


    def _disable_wifi_client(self):
        pass
        # os.system("sudo mv /etc/wpa-supplicant.conf")

    def _wifi_list(self):
        pass
    def _wifi connect(self, ssid, password):
        pass

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
