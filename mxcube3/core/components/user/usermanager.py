import logging
import json
import uuid
import datetime
import time

import flask
import flask_security
import flask_login
import flask_socketio

from mxcube3.core.components.component_base import ComponentBase
from mxcube3.core.models.usermodels import User
from mxcube3.core.util.networkutils import is_local_host, remote_addr
from mxcube3.core.util.convertutils import convert_to_dict

from mxcubecore import HardwareRepository as HWR


class BaseUserManager(ComponentBase):
    def __init__(self, app, config):
        super().__init__(app, config)

    def get_observers(self):
        return [
            user
            for user in User.query.all()
            if ((not user.in_control) and user.is_authenticated and user.is_active)
        ]

    def get_operator(self):
        user = None

        for _u in User.query.all():
            if _u.in_control:
                user = _u
                break

        return user

    def is_operator(self):
        return getattr(flask_login.current_user, "in_control", False)

    def logged_in_users(self, exclude_inhouse=False):
        users = [user["loginID"] for user in self.app.USERS.values()]

        if exclude_inhouse:
            if isinstance(HWR.beamline.session.in_house_users[0], tuple):
                ih_users = [
                    "%s%s" % (p, c) for (p, c) in HWR.beamline.session.in_house_users
                ]
            else:
                ih_users = HWR.beamline.session.in_house_users
            users = [user for user in users if user not in ih_users]

        return users

    def get_user(self, username):
        user = None

        for _u in User.query.all():
            if _u.username == username:
                user = _u

        return user

    def set_operator(self, username):
        user = None

        for _u in User.query.all():
            if _u.username == username:
                self.db_set_in_control(_u, True)
                user = _u
            else:
                self.db_set_in_control(_u, False)

        return user

    def emit_observers_changed(self, message=""):
        operator = self.app.usermanager.get_operator()

        data = {
            "observers": [_u.todict() for _u in self.app.usermanager.get_observers()],
            "message": message,
            "operator": operator.todict() if operator else {},
        }

        self.app.server.emit("observersChanged", data, namespace="/hwr")

    def update_operator(self, new_login=False):
        active_in_control = False

        for _u in User.query.all():
            if _u.is_authenticated and _u.in_control:
                active_in_control = True
            else:
                self.db_set_in_control(_u, False)

        # If new login and new observer login, clear nickname
        # so that the user get an opertunity to set one
        if new_login:
            flask_login.current_user.nickname = ""

        # If no user is currently in control set this user to be
        # in control
        if not active_in_control:
            if HWR.beamline.lims.loginType.lower() != "user":
                flask_login.current_user.nickname = self.app.lims.get_proposal(flask_login.current_user)
            else:
                flask_login.current_user.nickname = flask_login.current_user.username

            self.db_set_in_control(flask_login.current_user, True)

        # Set active proposal to that of the active user
        for _u in User.query.all():
            if _u.is_authenticated and _u.in_control:
                if HWR.beamline.lims.loginType.lower() != "user":
                    self.app.lims.select_proposal(
                        self.app.lims.get_proposal(_u)
                    )

    def handle_disconnect(self, username):
        time.sleep(120)

        user = self.get_user(username)

        if user.disconnect_timestamp:
            dt = datetime.datetime.now() - user.disconnect_timestamp

            # Disconnected for more than a minute
            if dt.seconds >= 120:
                logging.getLogger("HWR").info("Client disconnected")

                user.active = False
                self.update_user(user)
                self.emit_observers_changed()

    def is_inhouse_user(self, user_id):
        user_id_list = [
            "%s%s" % (code, number)
            for (code, number) in HWR.beamline.session.in_house_users
        ]

        return user_id in user_id_list

    # Abstract method to be implemented by concrete implementation
    def _login(self, login_id, password):
        pass

    def login(self, login_id, password):
        try:
            login_res = self._login(login_id, password)
        except BaseException:
            raise
        else:
            if not "sid" in flask.session:
                flask.session["sid"] = str(uuid.uuid4())

            user = self.db_create_user(login_id, password, login_res)
            self.app.server.user_datastore.activate_user(user)
            flask_security.login_user(user, remember=True)

            # Important to make flask_security user tracking work
            self.app.server.security.datastore.commit()

            address, barcode = self.app.sample_changer.get_loaded_sample()

            # If A sample is mounted (and not already marked as such),
            # get sample changer contents and add mounted sample to the queue
            if not self.app.sample_changer.get_current_sample() and address:
                self.app.sample_changer.get_sample_list()

            # For the moment not loading queue from persistent storage (redis),
            # uncomment to enable loading.
            # self.app.queue.load_queue(session)
            # logging.getLogger('MX3.HWR').info('Loaded queue')
            logging.getLogger("MX3.HWR").info(
                "[QUEUE] %s " % self.app.queue.queue_to_json()
            )

            self.update_operator(new_login=True)
            self.emit_observers_changed()

            msg = "User %s signed in" % user.username
            logging.getLogger("MX3.HWR").info(msg)

            return login_res["status"]

    # Abstract method to be implemented by concrete implementation
    def _signout(self):
        pass

    def signout(self):
        self._signout()
        user = flask_login.current_user

        # If operator logs out clear queue and sample list
        if self.is_operator():
            self.app.queue.save_queue(flask.session)
            self.app.queue.clear_queue()
            HWR.beamline.sample_view.clear_all()
            self.app.lims.init_sample_list()

            self.app.queue.init_queue_settings()

            if hasattr(HWR.beamline.session, "clear_session"):
                HWR.beamline.session.clear_session()

            self.app.CURRENTLY_MOUNTED_SAMPLE = ""

            self.db_set_in_control(flask_login.current_user, False)

            msg = "User %s signed out" % user.username
            logging.getLogger("MX3.HWR").info(msg)

        self.app.server.user_datastore.deactivate_user(user)
        flask_security.logout_user()
        self.emit_observers_changed()

    def is_authenticated(self):
        return flask_login.current_user.is_authenticated()

    def login_info(self):
        res = {
            "synchrotronName": HWR.beamline.session.synchrotron_name,
            "beamlineName": HWR.beamline.session.beamline_name,
            "loggedIn": False,
            "loginType": HWR.beamline.lims.loginType.title(),
            "proposalList": [],
            "user": {
                "username": "",
                "email": "",
                "isstaff": "",
                "nickname": "",
                "inControl": "",
                "ip": "",
            },
        }

        if not flask_login.current_user.is_anonymous:
            login_info = convert_to_dict(json.loads(flask_login.current_user.limsdata))

            self.update_operator()

            proposal_list = [
                {
                    "code": prop["Proposal"]["code"],
                    "number": prop["Proposal"]["number"],
                    "proposalId": prop["Proposal"]["proposalId"],
                    "title": prop["Proposal"]["title"],
                    "person": prop["Person"]["familyName"],
                }
                for prop in login_info.get("proposalList", [])
            ]

            res = {
                "synchrotronName": HWR.beamline.session.synchrotron_name,
                "beamlineName": HWR.beamline.session.beamline_name,
                "loggedIn": True,
                "loginType": HWR.beamline.lims.loginType.title(),
                "proposalList": proposal_list,
                "rootPath": HWR.beamline.session.get_base_image_directory(),
                "user": flask_login.current_user.todict(),
            }

            res["selectedProposal"] = "%s%s" % (
                HWR.beamline.session.proposal_code,
                HWR.beamline.session.proposal_number,
            )

            res["selectedProposalID"] = HWR.beamline.session.proposal_id

        return flask_login.current_user, res

    def update_user(self, user):
        self.app.server.user_datastore.put(user)
        self.app.server.user_datastore.commit()

    def _get_configured_roles(self, user):
        roles = set()

        _ihs = ["%s%s" % prop for prop in HWR.beamline.session.in_house_users]

        if self.config.inhouse_is_staff and user in _ihs:
            roles.add("staff")

        for _u in self.config.users:
            if _u.username == user:
                roles.add(_u.role)
                break

        return list(roles)

    def db_create_user(self, user, password, lims_data):
        sid = flask.session["sid"]
        user_datastore = self.app.server.user_datastore
        username = f"{user}-{sid}"

        # Make sure that the roles staff and incontrol always
        # exists
        if not user_datastore.find_role("staff"):
            user_datastore.create_role(name="staff")
            user_datastore.create_role(name="incontrol")
            self.app.server.user_datastore.commit()

        _u = user_datastore.find_user(username=username)

        if not _u:
            user_datastore.create_user(
                username=username,
                password=flask_security.hash_password("password"),
                nickname=user,
                session_id=sid,
                selected_proposal=user,
                limsdata=json.dumps(lims_data),
                roles=self._get_configured_roles(user)
            )
        else:
            _u.limsdata = json.dumps(lims_data)
            user_datastore.append_roles(_u, self._get_configured_roles(user))

        self.app.server.user_datastore.commit()

        return user_datastore.find_user(username=username)

    def db_set_in_control(self, user, control):
        user_datastore = self.app.server.user_datastore

        if control:
            for _u in User.query.all():
                if _u.username == user.username:
                    _u.in_control = True
                else:
                    _u.in_control = False

                user_datastore.put(_u)
        else:
            _u = user_datastore.find_user(username=user.username)
            _u.in_control = control
            user_datastore.put(_u)

        self.app.server.user_datastore.commit()


class UserManager(BaseUserManager):
    def __init__(self, app, config):
        super().__init__(app, config)

    def _login(self, login_id, password):
        login_res = self.app.lims.lims_login(login_id, password, create_session=False)
        inhouse = self.is_inhouse_user(login_id)

        info = {
            "valid": self.app.lims.lims_valid_login(login_res),
            "local": is_local_host(),
            "existing_session": self.app.lims.lims_existing_session(login_res),
            "inhouse": inhouse,
        }

        _users = self.logged_in_users(exclude_inhouse=True)

        # Only allow in-house log-in from local host
        if inhouse and not (inhouse and is_local_host()):
            raise Exception("In-house only allowed from localhost")

        # Only allow other users to log-in if they are from the same proposal
        if (not inhouse) and _users and (login_id not in _users):
            raise Exception("Another user is already logged in")

        # Only allow local login when remote is disabled
        if not self.app.ALLOW_REMOTE and not is_local_host():
            raise Exception("Remote access disabled")

        # Only allow remote logins with existing sessions
        if self.app.lims.lims_valid_login(login_res) and is_local_host():
            if not self.app.lims.lims_existing_session(login_res):
                login_res = self.app.lims.create_lims_session(login_res)

            msg = "[LOGIN] Valid login from local host (%s)" % str(info)
            logging.getLogger("MX3.HWR").info(msg)
        elif self.app.lims.lims_valid_login(
            login_res
        ) and self.app.lims.lims_existing_session(login_res):
            msg = "[LOGIN] Valid remote login from %s with existing session (%s)"
            msg += msg % (remote_addr(), str(info))
            logging.getLogger("MX3.HWR").info(msg)
        else:
            logging.getLogger("MX3.HWR").info("Invalid login %s" % info)
            raise Exception(str(info))

        return login_res

    def _signout(self):
        pass
