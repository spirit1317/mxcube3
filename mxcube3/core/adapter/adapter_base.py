import logging

from mxcube3.core.util.adapterutils import get_adapter_cls_from_hardware_object
from mxcube3.core.models.adaptermodels import HOModel, HOActuatorModel


class AdapterBase:
    """Hardware Object Adapter Base class"""

    def __init__(self, ho, role, app, **kwargs):
        """
        Args:
            (object): Hardware object to mediate for.
            (str): The name of the object
        """
        self.app = app
        self._ho = ho
        self._name = role
        self._available = True
        self._read_only = False
        self._type = type(self).__name__.replace("Adapter", "").upper()
        self._unique = True

    def get_adapter_id(self, ho=None):
        ho = self._ho if not ho else ho
        return self.app.mxcubecore._get_adapter_id(ho)

    def _add_adapter(self, attr_name, ho, adapter_cls=None):
        adapter_cls = (
            adapter_cls if adapter_cls else get_adapter_cls_from_hardware_object(ho)
        )

        _id = f"{self.get_adapter_id()}.{attr_name}"
        adapter_instance = adapter_cls(ho, _id, self.app)
        self.app.mxcubecore._add_adapter(_id, adapter_cls, ho, adapter_instance)

        setattr(self, attr_name, adapter_instance)

    def _set_value(self):
        pass

    def _get_value(self):
        pass

    def execute_command(self, cmd_name, args):
        self._ho.execute_exported_command(cmd_name, args)

    @property
    def adapter_type(self):
        """
        Returns:
            (str): The data type of the value 
        """
        return self._type

    @property
    def ho(self):
        """
        Underlaying HardwareObject
        Returns:
            (object): HardwareObject
        """
        return self._ho

    # Abstract method
    def state(self):
        """
        Retrieves the state of the underlying hardware object and converts it to a str
        that can be used by the javascript front end.
        Returns:
            (str): The state
        """
        return self._ho.get_state().name

    # Abstract method
    def msg(self):
        """
        Returns a message describing the current state. should be used to communicate 
        details of the state to the user.
        Returns:
            (str): The message string.
        """
        return ""

    def read_only(self):
        """
        Returns true if the hardware object is read only, set_value can not be called
        Returns:
            (bool): True if read enly.
        """
        return self._read_only

    def available(self):
        """
        Check if the hardware object is considered to be available/online/enabled
        Returns:
            (bool): True if available.
        """
        return self._available

    def attributes(self):
        if getattr(self._ho, "pydantic_model", None):
            return self._ho.exported_attributes
        else:
            return {}

    def commands(self):
        return ()

    def state_change(self, *args, **kwargs):
        """
        Signal handler to be used for sending the state to the client via
        socketIO
        """
        self.app.server.emit("beamline_value_change", self.dict(), namespace="/hwr")

    def _dict_repr(self):
        """
        Dictionary representation of the hardware object.
        Returns:
            (dict): The dictionary.
        """
        try:
            data = {
                "name": self._name,
                "state": self.state(),
                "msg": self.msg(),
                "type": self._type,
                "available": self.available(),
                "readonly": self.read_only(),
                "commands": self.commands(),
                "attributes": self.attributes(),
            }

        except Exception as ex:
            # Return a default representation if there is a problem retrieving
            # any of the attributes
            self._available = False

            data = {
                "name": self._name,
                "state": "UNKNOWN",
                "msg": "Exception: %s" % str(ex),
                "type": "FLOAT",
                "available": self.available(),
                "readonly": False,
                "attributes": {}
            }

            logging.getLogger("MX3.HWR").exception(
                f"Failed to get dictionary representation of {self._name}"
            )

        return data

    def data(self):
        return HOModel(**self._dict_repr())

    def dict(self):
        return self.data().dict()


class ActuatorAdapterBase(AdapterBase):
    def __init__(self, ho, *args, **kwargs):
        """
        Args:
            (object): Hardware object to mediate for.
            (str): The name of the object.
        """
        super(ActuatorAdapterBase, self).__init__(ho, *args, **kwargs)
        self._unique = False

        try:
            self._read_only = ho.read_only
        except AttributeError:
            pass

    # Dont't limit rate this method with utils.LimitRate, all sub-classes
    # will share this method thus all methods will be effected if limit rated.
    # Rather LimitRate the function calling this one.
    def value_change(self, *args, **kwargs):
        """
        Signal handler to be used for sending values to the client via
        socketIO.
        """
        data = {"name": self._name, "value": args[0]}
        self.app.server.emit("beamline_value_change", data, namespace="/hwr")

    # Abstract method
    def set_value(self, value):
        """
        Sets a value on underlying hardware object.
        Args:
            value(float): Value to be set.
        Returns:
            (str): The actual value, after being set.
        Raises:
            ValueError: When conversion or treatment of value fails.
            StopItteration: When a value change was interrupted (abort/cancel).
        """
        try:
            self._set_value(value)
            data = self.dict()
        except ValueError as ex:
            self._available = False
            data = self.dict()
            data["state"] = "UNUSABLE"
            data["msg"] = str(ex)
            logging.getLogger("MX3.HWR").error("Error setting bl attribute: " + str(ex))

        return data

    # Abstract method
    def get_value(self):
        """
        Retrieve value from underlying hardware object.
        Returns:
            (str): The value.
        Raises:
            ValueError: When value for any reason can't be retrieved.
        """
        return self._get_value().value

    # Abstract method
    def stop(self):
        """
        Stop an action/movement.
        """

    def limits(self):
        """
        Read the energy limits.
        Returns:
            (tuple): Two floats (min, max).
        Raises:
            ValueError: When limits for any reason can't be retrieved.
        """
        try:
            # Limits are None when not configured, convert them to -1, -1
            # as we are returning floats
            return (0, 0) if None in self._ho.get_limits() else self._ho.get_limits()
        except (AttributeError, TypeError):
            raise ValueError("Could not get limits")

    def _dict_repr(self):
        """Dictionary representation of the hardware object.
        Returns:
            (dict): The dictionary.
        """
        data = super(ActuatorAdapterBase, self)._dict_repr()

        try:
            data.update({"value": self.get_value(), "limits": self.limits()})
        except Exception as ex:
            self._available = False
            data.update(
                {
                    "value": 0,
                    "limits": (0, 0),
                    "type": "FLOAT",
                    "msg": "Exception %s" % str(ex),
                }
            )

        return data

    def data(self) -> HOActuatorModel:
        return HOActuatorModel(**self._dict_repr())
