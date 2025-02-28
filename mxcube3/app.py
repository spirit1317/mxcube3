"""
Module that contains application wide settings and state as well as functions
for accessing and manipulating those.
"""
import os
import sys
import logging
import traceback
import atexit
import json

from pathlib import Path
from logging import StreamHandler, NullHandler
from logging.handlers import TimedRotatingFileHandler

from mxcubecore import HardwareRepository as HWR
from mxcubecore import removeLoggingHandlers
from mxcubecore.HardwareObjects import queue_entry
from mxcubecore.utils.conversion import make_table

from mxcube3.logging_handler import MX3LoggingHandler
from mxcube3.core.util.adapterutils import get_adapter_cls_from_hardware_object
from mxcube3.core.adapter.adapter_base import AdapterBase
from mxcube3.core.components.component_base import import_component
from mxcube3.core.components.lims import Lims
from mxcube3.core.components.chat import Chat
from mxcube3.core.components.samplechanger import SampleChanger
from mxcube3.core.components.beamline import Beamline
from mxcube3.core.components.sampleview import SampleView
from mxcube3.core.components.queue import Queue
from mxcube3.core.components.workflow import Workflow


removeLoggingHandlers()


class MXCUBECore:
    # The HardwareRepository object
    hwr = None

    # Below, all the HardwareObjects made available through this module,
    # Initialized by the init function

    # XMLRPCServer
    actions = None
    # Plotting
    plotting = None

    adapter_dict = {}

    @staticmethod
    def exit_with_error(msg):
        """
        Writes the traceback and msg to the log and exits the application

        :param str msg: Additional message to write to log

        """
        logging.getLogger("HWR").error(traceback.format_exc())

        if msg:
            logging.getLogger("HWR").error(msg)

        msg = "Could not initialize one or several hardware objects, stopped "
        msg += "at first error !"

        logging.getLogger("HWR").error(msg)
        logging.getLogger("HWR").error("Quitting server !")
        sys.exit(-1)

    @staticmethod
    def init(app):
        """
        Initializes the HardwareRepository with XML files read from hwdir.

        The hwr module must be imported at the very beginning of the application
        start-up to function correctly.

        This method can however be called later, so that initialization can be
        done when one wishes.

        :param app: FIXME ???

        :return: None
        """
        from mxcube3.core.adapter.beamline_adapter import BeamlineAdapter

        fname = os.path.dirname(__file__)
        HWR.add_hardware_objects_dirs([os.path.join(fname, "HardwareObjects")])
        # rhfogh 20210916. The change allows (me) simpler configuration handling
        # and because of changes in init_hardware_repository does not change
        # current functionality.
        _hwr = HWR.get_hardware_repository()

        MXCUBECore.hwr = _hwr

        try:
            MXCUBECore.beamline = BeamlineAdapter(HWR.beamline, MXCUBEApplication)
            MXCUBECore.adapt_hardware_objects(app)
        except Exception:
            msg = "Could not initialize one or several hardware objects, "
            msg += "stopped at first error ! \n"
            msg += "Make sure That all devices servers are running \n"
            msg += "Make sure that the detector software is running \n"
            MXCUBECore.exit_with_error(msg)

    @staticmethod
    def _get_object_from_id(_id):
        if _id in MXCUBECore.adapter_dict:
            return MXCUBECore.adapter_dict[_id]["adapter"]

    @staticmethod
    def _get_adapter_id(ho):
        try:
            if ho.username != None:
                _id = ho.username
            else:
                _id = ho.name()[1:]
        except:
            _id = ho.name()[1:]

        return _id.replace(" ", "_").lower()

    @staticmethod
    def _add_adapter(_id, adapter_cls, ho, adapter_instance):
        if _id not in MXCUBECore.adapter_dict:
            MXCUBECore.adapter_dict[_id] = {
                "id": str(_id),
                "adapter_cls": adapter_cls.__name__,
                "ho": ho.name()[1:],
                "adapter": adapter_instance,
            }
        else:
            logging.getLogger("MX3.HWR").warning(
                f"Skipping {ho.name()}, id: {_id} already exists"
            )

    @staticmethod
    def get_adapter(_id):
        return MXCUBECore._get_object_from_id(_id)

    @staticmethod
    def adapt_hardware_objects(app):
        adapter_config = app.CONFIG.app.adapter_properties

        # NB. We should investigate why the list hardware_objects
        # is updated internaly in mxcubecore
        hwobject_list = [item for item in MXCUBECore.hwr.hardware_objects]

        for ho_name in hwobject_list:
            # Go through all hardware objects exposed by mxcubecore
            # hardware repository set id to username if its deinfed
            # use the name otherwise (file name without extension)
            ho = MXCUBECore.hwr.get_hardware_object(ho_name)

            if not ho:
                continue

            _id = MXCUBECore._get_adapter_id(ho)

            # Try to use the interface exposed by abstract classes in mxcubecore to adapt
            # the object
            adapter_cls = get_adapter_cls_from_hardware_object(ho)

            if adapter_cls:
                try:
                    adapter_instance = adapter_cls(ho, _id, app, **dict(adapter_config))
                    logging.getLogger("MX3.HWR").info("Added adapter for %s" % _id)
                except:
                    logging.getLogger("MX3.HWR").exception(
                        "Could not add adapter for %s" % _id
                    )
                    logging.getLogger("MX3.HWR").info("%s not available" % _id)
                    adapter_cls = AdapterBase
                    adapter_instance = AdapterBase(None, _id, app)

                MXCUBECore._add_adapter(_id, adapter_cls, ho, adapter_instance)
            else:
                logging.getLogger("MX3.HWR").info("No adapter for %s" % _id)

        print(
            make_table(
                ["Name", "Adapter", "HO filename"],
                [
                    [item["id"], item["adapter_cls"], item["ho"]]
                    for item in MXCUBECore.adapter_dict.values()
                ],
            )
        )


class MXCUBEApplication:
    # Below variables used for internal application state

    # SampleID and sample data of currently mounted sample, to handle samples
    # that are not mounted by sample changer.
    CURRENTLY_MOUNTED_SAMPLE = ""

    # Sample location of sample that are in process of being mounted
    SAMPLE_TO_BE_MOUNTED = ""

    # Method used for sample centring
    CENTRING_METHOD = queue_entry.CENTRING_METHOD.LOOP

    # Look up table for finding the limsID for a corresponding queueID (QueueNode)
    NODE_ID_TO_LIMS_ID = {}

    # Initial file list for user, initialized at login, for creating automatic
    # run numbers
    INITIAL_FILE_LIST = []

    # Lookup table for sample changer location to data matrix or
    # data matrix to location
    SC_CONTENTS = {"FROM_CODE": {}, "FROM_LOCATION": {}}

    # Current sample list, with tasks
    SAMPLE_LIST = {"sampleList": {}, "sampleOrder": []}

    # Users currently logged in
    USERS = {}

    # Path to video device (i.e. /dev/videoX)
    VIDEO_FORMAT = "MPEG1"

    # Contains the complete client side ui state, managed up state_storage.py
    UI_STATE = dict()
    TEMP_DISABLED = []

    # Below variables used for application wide settings

    # Enabled or Disable remote usage
    ALLOW_REMOTE = False

    # Enable timeout gives control (if ALLOW_REMOTE is True)
    TIMEOUT_GIVES_CONTROL = False

    # Enable automatic Mountie of sample when queue executed in
    # "automatic/pipeline" mode
    AUTO_MOUNT_SAMPLE = False

    # Automatically add and execute diffraction plans coming from
    # characterizations
    AUTO_ADD_DIFFPLAN = False

    # Number of sample snapshots taken before collect
    NUM_SNAPSHOTS = 4

    CONFIG = None

    mxcubecore = MXCUBECore()

    server = None

    @staticmethod
    def init(server, allow_remote, ra_timeout, video_device, log_fpath, cfg):
        """
        Initializes application wide variables, sample video stream, and applies

        :param hwr: HardwareRepository module
        :param bool allow_remote: Allow remote usage, True else False
        :param bool ra_timeout: Timeout gives control, True else False
        :param bool video_device: Path to video device

        :return None:
        """
        logging.getLogger("MX3.HWR").info("Starting MXCuBE3...")
        MXCUBEApplication.server = server
        MXCUBEApplication.ALLOW_REMOTE = allow_remote
        MXCUBEApplication.TIMEOUT_GIVES_CONTROL = ra_timeout
        MXCUBEApplication.CONFIG = cfg

        MXCUBEApplication.mxcubecore.init(MXCUBEApplication)

        if video_device:
            MXCUBEApplication.init_sample_video(video_device)

        MXCUBEApplication.init_logging(log_fpath)

        _UserManagerCls = import_component(
            cfg.app.usermanager, package="components.user"
        )

        MXCUBEApplication.queue = Queue(MXCUBEApplication, {})
        MXCUBEApplication.lims = Lims(MXCUBEApplication, {})
        MXCUBEApplication.usermanager = _UserManagerCls(
            MXCUBEApplication, cfg.app.usermanager
        )
        MXCUBEApplication.chat = Chat(MXCUBEApplication, {})
        MXCUBEApplication.sample_changer = SampleChanger(MXCUBEApplication, {})
        MXCUBEApplication.beamline = Beamline(MXCUBEApplication, {})
        MXCUBEApplication.sample_view = SampleView(MXCUBEApplication, {})
        MXCUBEApplication.workflow = Workflow(MXCUBEApplication, {})

        MXCUBEApplication.init_signal_handlers()
        atexit.register(MXCUBEApplication.app_atexit)

        # Install server-side UI state storage
        MXCUBEApplication.init_state_storage()

        # MXCUBEApplication.load_settings()

    @staticmethod
    def init_sample_video(video_device):
        """
        Initializes video streaming from video device <video_device>, relies on
        v4l2loopback kernel module to write the sample video stream to
        <video_device>.

        The streaming is handled by the streaming module

        :param str video_device: Path to video device, i.e. /dev/videoX

        :return: None
        """
        try:
            HWR.beamline.sample_view.camera.start_streaming()
        except Exception as ex:
            msg = "Could not initialize video, error was: "
            msg += str(ex)
            logging.getLogger("HWR").info(msg)

    @staticmethod
    def init_signal_handlers():
        """
        Connects the signal handlers defined in routes/signals.py to the
        corresponding signals/events
        """
        try:
            MXCUBEApplication.queue.init_signals(HWR.beamline.queue_model)
        except Exception:
            sys.excepthook(*sys.exc_info())

        try:
            MXCUBEApplication.sample_view.init_signals()
        except Exception:
            sys.excepthook(*sys.exc_info())

        try:
            MXCUBEApplication.sample_changer.init_signals()
        except Exception:
            sys.excepthook(*sys.exc_info())

        try:
            MXCUBEApplication.beamline.init_signals()
            MXCUBEApplication.beamline.diffractometer_init_signals()
        except Exception:
            sys.excepthook(*sys.exc_info())

    @staticmethod
    def init_logging(log_file):
        """
        :param str log_file: Path to log file

        :return: None
        """
        removeLoggingHandlers()

        fmt = "%(asctime)s |%(name)-7s|%(levelname)-7s| %(message)s"
        log_formatter = logging.Formatter(fmt)

        if log_file:
            log_file_handler = TimedRotatingFileHandler(
                log_file, when="midnight", backupCount=7
            )
            os.chmod(log_file, 0o666)
            log_file_handler.setFormatter(log_formatter)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(NullHandler())

        custom_log_handler = MX3LoggingHandler(MXCUBEApplication.server)
        custom_log_handler.setLevel(logging.DEBUG)
        custom_log_handler.setFormatter(log_formatter)

        exception_logger = logging.getLogger("exceptions")
        hwr_logger = logging.getLogger("HWR")
        mx3_hwr_logger = logging.getLogger("MX3.HWR")
        user_logger = logging.getLogger("user_level_log")
        queue_logger = logging.getLogger("queue_exec")
        stdout_log_handler = StreamHandler(sys.stdout)
        stdout_log_handler.setFormatter(log_formatter)

        for logger in (
            exception_logger,
            hwr_logger,
            user_logger,
            mx3_hwr_logger,
            queue_logger,
        ):
            logger.addHandler(custom_log_handler)
            logger.addHandler(stdout_log_handler)

            if log_file:
                logger.addHandler(log_file_handler)

    @staticmethod
    def init_state_storage():
        """
        Set up of server side state storage, the UI state of the client is
        stored on the server
        """
        from mxcube3 import state_storage

        state_storage.init()

    @staticmethod
    def get_ui_properties():
        # Add type information to each component retrieved from the beamline adapter
        # (either via config or via mxcubecore.beamline)
        for _item_name, item_data in MXCUBEApplication.CONFIG.app.ui_properties.items():
            for component_data in item_data.components:
                try:
                    mxcore = MXCUBEApplication.mxcubecore
                    adapter = mxcore.get_adapter(component_data.attribute)
                    adapter_cls_name = type(adapter).__name__
                    value_type = adapter.adapter_type
                except AttributeError as ex:
                    adapter_cls_name = ""
                    value_type = ""
                else:
                    adapter_cls_name = adapter_cls_name.replace("Adapter", "")

                if not component_data.object_type:
                    component_data.object_type = adapter_cls_name

                if not component_data.value_type:
                    component_data.value_type = value_type

        return {key: value.dict() for (key, value) in MXCUBEApplication.CONFIG.app.ui_properties.items()}

    @staticmethod
    def save_settings():
        """
        Saves all application wide variables to disk, stored-mxcube-session.json
        """
        queue = MXCUBEApplication.queue.queue_to_dict(
            HWR.beamline.queue_model.get_model_root()
        )

        # For the moment not storing USERS

        data = {
            "QUEUE": queue,
            "CURRENTLY_MOUNTED_SAMPLE": MXCUBEApplication.CURRENTLY_MOUNTED_SAMPLE,
            "SAMPLE_TO_BE_MOUNTED": MXCUBEApplication.SAMPLE_TO_BE_MOUNTED,
            "CENTRING_METHOD": MXCUBEApplication.CENTRING_METHOD,
            "NODE_ID_TO_LIMS_ID": MXCUBEApplication.NODE_ID_TO_LIMS_ID,
            "INITIAL_FILE_LIST": MXCUBEApplication.INITIAL_FILE_LIST,
            "SC_CONTENTS": MXCUBEApplication.SC_CONTENTS,
            "SAMPLE_LIST": MXCUBEApplication.SAMPLE_LIST,
            "TEMP_DISABLED": MXCUBEApplication.TEMP_DISABLED,
            "ALLOW_REMOTE": MXCUBEApplication.ALLOW_REMOTE,
            "TIMEOUT_GIVES_CONTROL": MXCUBEApplication.TIMEOUT_GIVES_CONTROL,
            "VIDEO_FORMAT": MXCUBEApplication.VIDEO_FORMAT,
            "AUTO_MOUNT_SAMPLE": MXCUBEApplication.AUTO_MOUNT_SAMPLE,
            "AUTO_ADD_DIFFPLAN": MXCUBEApplication.AUTO_ADD_DIFFPLAN,
            "NUM_SNAPSHOTS": MXCUBEApplication.NUM_SNAPSHOTS,
            "UI_STATE": MXCUBEApplication.UI_STATE,
        }

        fname = Path("/tmp/stored-mxcube-session.json")
        fname.touch(exist_ok=True)

        with open(fname, "w+") as fp:
            json.dump(data, fp)

    @staticmethod
    def load_settings():
        """
        Loads application wide variables from "stored-mxcube-session.json"
        """
        with open("/tmp/stored-mxcube-session.json", "r") as f:
            data = json.load(f)

        MXCUBEApplication.queue.load_queue_from_dict(data.get("QUEUE", {}))

        MXCUBEApplication.CENTRING_METHOD = data.get(
            "CENTRING_METHOD", queue_entry.CENTRING_METHOD.LOOP
        )
        MXCUBEApplication.NODE_ID_TO_LIMS_ID = data.get("NODE_ID_TO_LIMS_ID", {})
        MXCUBEApplication.SC_CONTENTS = data.get(
            "SC_CONTENTS", {"FROM_CODE": {}, "FROM_LOCATION": {}}
        )
        MXCUBEApplication.SAMPLE_LIST = data.get(
            "SAMPLE_LIST", {"sampleList": {}, "sampleOrder": []}
        )
        MXCUBEApplication.ALLOW_REMOTE = data.get("ALLOW_REMOTE", False)
        MXCUBEApplication.TIMEOUT_GIVES_CONTROL = data.get(
            "TIMEOUT_GIVES_CONTROL", False
        )
        MXCUBEApplication.AUTO_MOUNT_SAMPLE = data.get("AUTO_MOUNT_SAMPLE", False)
        MXCUBEApplication.AUTO_ADD_DIFFPLAN = data.get("AUTO_ADD_DIFFPLAN", False)
        MXCUBEApplication.NUM_SNAPSHOTS = data.get("NUM_SNAPSHOTS", False)
        MXCUBEApplication.UI_STATE = data.get("UI_STATE", {})

    @staticmethod
    def app_atexit():
        MXCUBEApplication.save_settings()
