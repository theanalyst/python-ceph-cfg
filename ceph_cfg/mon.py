# Import Python Libs
from __future__ import absolute_import
import logging
import os
import pwd
import tempfile
import shutil
import time

# Local imports
from . import keyring
from . import mdl_query
from . import mdl_updater
from . import model
from . import presenter
from . import utils
from . import service
from . import util_which


log = logging.getLogger(__name__)


def Property(func):
    return property(**func())

class Error(Exception):
    """
    Error
    """

    def __str__(self):
        doc = self.__doc__.strip()
        return ': '.join([doc] + [str(a) for a in self.args])


class mon_implementation_base(object):
    def __init__(self, mdl):
        self.model = mdl
        self.model.init = "systemd"
        self.init_system = service.init_system(init_type=self.model.init)


    def _execute(self, arguments):
        return utils.execute_local_command(arguments)


    def _create_monmap(self, path_monmap):
        """
        create_monmap file
        """
        if not os.path.isfile(path_monmap):
            arguments = [
                "monmaptool",
                "--create",
                "--fsid",
                self.model.cluster_uuid,
                path_monmap
                ]
            output = utils.execute_local_command(arguments)
            if output["retcode"] != 0:
                    raise Error("Failed executing '%s' Error rc=%s, stdout=%s stderr=%s" % (
                        " ".join(arguments),
                        output["retcode"],
                        output["stdout"],
                        output["stderr"])
                        )
            for name, addr in self.model.mon_members:
                arguments = [
                        "monmaptool",
                        "--add",
                        name,
                        addr,
                        path_monmap
                        ]
                output = utils.execute_local_command(arguments)
                if output["retcode"] != 0:
                    raise Error("Failed executing '%s' Error rc=%s, stdout=%s stderr=%s" % (
                        " ".join(arguments),
                        output["retcode"],
                        output["stdout"],
                        output["stderr"])
                        )
        return True


    def mon_is(self, **kwargs):
        """
        Is this a mon node

        cluster_name
            Set the cluster name. Defaults to "ceph".

        cluster_uuid
            Set the cluster UUID. Defaults to value found in ceph config file.
        """
        u = mdl_updater.model_updater(self.model)
        u.hostname_refresh()
        try:
            u.defaults_refresh()
        except:
            return False
        u.load_confg(self.model.cluster_name)
        u.mon_members_refresh()
        q = mdl_query.mdl_query(self.model)
        return q.mon_is()


    def status(self, **kwargs):
        """
        Get status from mon deamon

        cluster_uuid
            Set the cluster UUID. Defaults to value found in ceph config file.

        cluster_name
            Set the cluster name. Defaults to "ceph".
        """
        u = mdl_updater.model_updater(self.model)
        u.hostname_refresh()
        try:
            u.defaults_refresh()
        except:
            return {}
        u.load_confg(self.model.cluster_name)
        u.mon_members_refresh()
        q = mdl_query.mdl_query(self.model)
        if not q.mon_is():
            raise Error("Not a mon node")
        u.mon_status()
        p = presenter.mdl_presentor(self.model)
        return p.mon_status()


    def quorum(self, **kwargs):
        """
        Is mon deamon in quorum

        Args:
            **kwargs: Arbitrary keyword arguments.
                cluster_uuid
                    Set the cluster UUID. Defaults to value found in ceph
                    config file.
                cluster_name
                    Set the cluster name. Defaults to "ceph".
        """
        u = mdl_updater.model_updater(self.model)
        u.hostname_refresh()
        try:
            u.defaults_refresh()
        except:
            raise Error("Could not get cluster details")
        u.load_confg(self.model.cluster_name)
        u.mon_members_refresh()
        u.mon_status()
        q = mdl_query.mdl_query(self.model)
        return q.mon_quorum()


    def _create_check_responding(self):
        """
        Check the mon service is runnign and responding.
        """
        q = mdl_query.mdl_query(self.model)
        if not q.mon_active():
            raise Error("mon service has died.")
        u = mdl_updater.model_updater(self.model)
        try:
            u.mon_status()
        except mdl_updater.Error:
            return False
        return True


    def _create_check_retry(self):
        """
        Check the mon service is started and responding with time out.

        On heavily overloaded hardware it can takes a while for the mon service
        to start
        """
        # Number of seconds before a time out.
        timeout = 60
        time_start = time.clock()
        time_end = time_start + timeout
        if self._create_check_responding():
            return True
        while time.clock() < time_end:
            log.info("Mon service did not start up, waiting.")
            time.sleep(5)
            log.info("Retrying mon service.")
            if self._create_check_responding():
                return True
        log.error("Timed out starting mon service")
        raise Error("Failed to get mon service status after '%s' seconds." % (timeout))


    def create(self, **kwargs):
        """
        Create a mon node

        Args:
            **kwargs: Arbitrary keyword arguments.
                cluster_uuid
                    Set the cluster UUID. Defaults to value found in ceph
                    config file.
                cluster_name
                    Set the cluster name. Defaults to "ceph".
        """
        if util_which.which_ceph_mon.path is None:
            raise Error("Could not find executable 'ceph-mon'")

        u = mdl_updater.model_updater(self.model)
        u.hostname_refresh()
        u.defaults_refresh()
        u.load_confg(self.model.cluster_name)
        u.mon_members_refresh()
        q = mdl_query.mdl_query(self.model)
        # if not q.mon_is():
        #     raise Error("Not a mon node")

        path_done_file = "/var/lib/ceph/mon/%s-%s/done" % (
                self.model.cluster_name,
                self.model.hostname
            )
        keyring_path_mon = keyring._get_path_keyring_mon_bootstrap(self.model.cluster_name, self.model.hostname)
        path_mon_dir = "/var/lib/ceph/mon/%s-%s" % (
                self.model.cluster_name,
                self.model.hostname
            )

        path_admin_keyring = keyring._get_path_keyring_admin(self.model.cluster_name)

        path_monmap = "/var/lib/ceph/tmp/%s.monmap" % (
                self.model.cluster_name
            )
        if os.path.isfile(path_done_file):
            log.debug("Mon done file exists:%s" % (path_done_file))
            if q.mon_active():
                return True
            arguments = [
                util_which.which_systemctl.path,
                "restart",
                "ceph-mon@%s" % (self.model.hostname)
                ]
            output = utils.execute_local_command(arguments)
            if output["retcode"] != 0:
                raise Error("Failed executing '%s' Error rc=%s, stdout=%s stderr=%s" % (
                    " ".join(arguments),
                    output["retcode"],
                    output["stdout"],
                    output["stderr"])
                    )

            # Error is servcie wont start
            if not q.mon_active():
                 raise Error("Failed to start monitor")
            return True

        if not os.path.isfile(keyring_path_mon):
            raise Error("Mon keyring missing")
        if not os.path.isfile(path_admin_keyring):
            raise Error("Admin keyring missing")

        try:
            tmpd = tempfile.mkdtemp()
            log.info("Create temp directory %s" %(tmpd))
            os.chown(tmpd, self.uid, self.gid)
            # In 'tmpd' we make the monmap and keyring.
            key_path = os.path.join(tmpd,"keyring")
            path_monmap = os.path.join(tmpd,"monmap")
            log.info("Create monmap %s" % (path_monmap))
            self._create_monmap(path_monmap)
            os.chown(path_monmap, self.uid, self.gid)
            arguments = [
                util_which.which_ceph_authtool.path,
                "--create-keyring",
                key_path,
                "--import-keyring",
                keyring_path_mon,
                ]
            output = utils.execute_local_command(arguments)
            if output["retcode"] != 0:
                raise Error("Failed executing '%s' Error rc=%s, stdout=%s stderr=%s" % (
                    " ".join(arguments),
                    output["retcode"],
                    output["stdout"],
                    output["stderr"]
                    ))
            arguments = [
                util_which.which_ceph_authtool.path,
                key_path,
                "--import-keyring",
                path_admin_keyring,
                ]
            output = utils.execute_local_command(arguments)
            if output["retcode"] != 0:
                raise Error("Failed executing '%s' Error rc=%s, stdout=%s stderr=%s" % (
                    " ".join(arguments),
                    output["retcode"],
                    output["stdout"],
                    output["stderr"]
                    ))
            # Now chown the new file
            os.chown(key_path, self.uid, self.gid)
            # Now clean the install area
            if os.path.isdir(path_mon_dir):
                log.info("Remove directory content %s" %(path_mon_dir))
                shutil.rmtree(path_mon_dir)
            if not os.path.isdir(path_mon_dir):
                log.info("Make directory %s" %(path_mon_dir))
                os.makedirs(path_mon_dir)
                os.chown(path_mon_dir, self.uid, self.gid)
            # now do install
            arguments = [
                    util_which.which_ceph_mon.path,
                    "--mkfs",
                    "-i",
                    self.model.hostname,
                    "--monmap",
                    path_monmap,
                    '--keyring',
                    key_path
                    ]
            output = self._execute(arguments)
            if output["retcode"] != 0:
                raise Error("Failed executing '%s' Error rc=%s, stdout=%s stderr=%s" % (
                    " ".join(arguments),
                    output["retcode"],
                    output["stdout"],
                    output["stderr"]
                    ))
            # check keyring created:
            path_mon_key = os.path.join(path_mon_dir, "keyring")
            if not os.path.isfile(path_mon_key):
                raise Error("Failed to create '%s'" % (path_mon_key))
            # Now start the service
            arguments = {
                'identifier' : self.model.hostname,
                'service' : "ceph-mon",
            }
            self.init_system.restart(**arguments)
            self.init_system.on_boot_enable(**arguments)
            self._create_check_retry()
            open(path_done_file, 'a').close()
        finally:
            log.info("Destroy temp directory %s" %(tmpd))
            shutil.rmtree(tmpd)
        return True


    def active(self, **kwargs):
        """
        Is mon deamon running
        """
        u = mdl_updater.model_updater(self.model)
        u.hostname_refresh()
        q = mdl_query.mdl_query(self.model)
        return q.mon_active()


class mod_user_root(mon_implementation_base):
    def __init__(self, mdl):
        mon_implementation_base.__init__(self, mdl)
        self.uid = 0
        self.gid = 0


class mod_user_ceph(mon_implementation_base):
    def __init__(self, mdl):
        mon_implementation_base.__init__(self, mdl)
        pwd_struct = pwd.getpwnam("ceph")
        self.uid = pwd_struct.pw_uid
        self.gid = pwd_struct.pw_gid


    def _execute(self,arguments):
        prefix = [
            "sudo",
            "-u",
            "ceph"
            ]
        return utils.execute_local_command(prefix + arguments)


class mon_facard(object):
    def __init__(self, **kwargs):
        self.model = model.model(**kwargs)
        self._clear_implementation()
        u = mdl_updater.model_updater(self.model)
        u.ceph_version_refresh()
        q = mdl_query.mdl_query(self.model)
        self.ceph_daemon_user = q.ceph_daemon_user()


    def _clear_implementation(self):
        self._ceph_daemon_user = None
        self._monImp = None


    @Property
    def ceph_daemon_user():
        doc = "key_type"

        def fget(self):
            return self._ceph_daemon_user


        def fset(self, user):
            if user is None:
                self._clear_implementation()
            implementation = None
            if user == "root":
                implementation = mod_user_root(self.model)
            if user == "ceph":
                implementation = mod_user_ceph(self.model)
            if implementation is None:
                raise Error("Invalid ceph_daemon_user")
            self._monImp = implementation
            self._ceph_daemon_user = user
            return self._ceph_daemon_user


        def fdel(self):
            self._clear_implementation()


        return locals()


    def create(self, **kwargs):
        """
        Create mon
        """
        if self._monImp is None:
            raise Error("Programming error: key type unset")
        return self._monImp.create(**kwargs)


    def quorum(self, **kwargs):
        if self._monImp is None:
            raise Error("Programming error: key type unset")
        return self._monImp.quorum(**kwargs)


    def status(self, **kwargs):
        if self._monImp is None:
            raise Error("Programming error: key type unset")
        return self._monImp.status(**kwargs)


    def is_mon(self, **kwargs):
        if self._monImp is None:
            raise Error("Programming error: key type unset")
        return self._monImp.mon_is(**kwargs)


    def active(self, **kwargs):
        if self._monImp is None:
            raise Error("Programming error: key type unset")
        return self._monImp.active(**kwargs)
