import io from 'socket.io-client';
import { addResponseMessage } from 'react-chat-widget';
import { addLogRecord } from './actions/logger';
import {
  setShapes,
  saveMotorPosition,
  updateMotorState,
  setBeamInfo,
  startClickCentring,
  updateShapes,
  setPixelsPerMm,
  videoMessageOverlay,
  setCurrentPhase,
} from './actions/sampleview';
import { setBeamlineAttrAction, setMachInfo } from './actions/beamline';
import {
  setActionState,
  addUserMessage,
  newPlot,
  plotData,
  plotEnd,
} from './actions/beamlineActions';
import {
  setStatus,
  addTaskResultAction,
  updateTaskLimsData,
  addTaskAction,
  sendStopQueue,
  setCurrentSample,
  addDiffractionPlanAction,
  setSampleAttribute,
} from './actions/queue';
import { collapseItem, showResumeQueueDialog } from './actions/queueGUI';
import { setLoading, showConnectionLostDialog } from './actions/general';

import { showWorkflowParametersDialog } from './actions/workflow';

import { incChatMessageCount, getRaState } from './actions/remoteAccess';

import { doSignOut, getLoginInfo } from './actions/login';

import {
  setSCState,
  setLoadedSample,
  setSCGlobalState,
  updateSCContents,
} from './actions/sampleChanger';

import { setEnergyScanResult } from './actions/taskResults';

import { CLICK_CENTRING } from './constants';

class ServerIO {
  constructor() {
    this.networkSocket = null;
    this.hwrSocket = null;
    this.loggingSocket = null;
    this.uiStateSocket = null;
    this.hwrsid = null;
    this.connected = false;

    this.uiStorage = {
      setItem: (key, value) => {
        this.uiStateSocket.emit('ui_state_set', [key, value]);
      },
      getItem: (key, cb) => {
        this.uiStateSocket.emit('ui_state_get', key, (value) => {
          cb(false, value);
        });
      },
      removeItem: (key) => {
        this.uiStateSocket.emit('ui_state_rm', key);
      },
      getAllKeys: (cb) => {
        this.uiStateSocket.emit('ui_state_getkeys', null, (value) => {
          cb(false, value);
        });
      },
    };
  }

  connectNetworkSocket(cb) {
    this.networkSocket = io.connect(
      `//${document.domain}:${window.location.port}/network`
    );
    this.networkSocket.on('connect', () => {
      cb(true);
      this.connected = true;
    });

    this.networkSocket.on('disconnect', () => {
      cb(false);
      this.connected = false;
    });
  }

  connectStateSocket(statePersistor) {
    this.uiStateSocket = io.connect(
      `//${document.domain}:${window.location.port}/ui_state`
    );

    this.uiStateSocket.on('state_update', (newState) => {
      statePersistor.rehydrate(JSON.parse(newState));
    });
  }

  // setRemoteAccessMaster(name, cb) {
  //   this.hwrSocket.emit('setRaMaster', { master: true, name }, cb);
  // }

  // setRemoteAccessObserver(name, cb) {
  //   this.hwrSocket.emit('setRaObserver', { master: true, name }, cb);
  // }

  disconnect() {
    this.hwrSocket.disconnect();
    this.hwrSocket.disconnect();
    this.loggingSocket.disconnect();
  }

  listen(store) {
    this.dispatch = store.dispatch;

    this.hwrSocket = io.connect(
      `//${document.domain}:${window.location.port}/hwr`
    );
    this.loggingSocket = io.connect(
      `//${document.domain}:${window.location.port}/logging`
    );

    this.loggingSocket.on('log_record', (record) => {
      this.dispatch(addUserMessage(record));
      this.dispatch(addLogRecord(record));
    });

    this.hwrSocket.on('ra_chat_message', (record) => {
      const { username } = store.getState().login.user;
      if (record.username !== username) {
        addResponseMessage(
          `${record.date} **${record.nickname}:** \n\n ${record.message}`
        );
        this.dispatch(incChatMessageCount());
      }
    });

    this.hwrSocket.on('motor_position', (record) => {
      this.dispatch(saveMotorPosition(record.name, record.position));
    });

    this.hwrSocket.on('motor_state', (record) => {
      this.dispatch(updateMotorState(record.name, record.state));
    });

    this.hwrSocket.on('update_shapes', (record) => {
      this.dispatch(setShapes(record.shapes));
    });

    this.hwrSocket.on('update_pixels_per_mm', (record) => {
      this.dispatch(setPixelsPerMm(record.pixelsPerMm));
    });

    this.hwrSocket.on('beam_changed', (record) => {
      this.dispatch(setBeamInfo(record.data));
    });

    this.hwrSocket.on('mach_info_changed', (info) => {
      this.dispatch(setMachInfo(info));
    });

    this.hwrSocket.on('beamline_value_change', (data) => {
      this.dispatch(setBeamlineAttrAction(data));
    });

    this.hwrSocket.on('grid_result_available', (data) => {
      this.dispatch(updateShapes([data.shape]));
    });

    this.hwrSocket.on('energy_scan_result', (data) => {
      this.dispatch(setEnergyScanResult(data.pk, data.ip, data.rm));
    });

    this.hwrSocket.on('update_task_lims_data', (record) => {
      this.dispatch(
        updateTaskLimsData(
          record.sample,
          record.taskIndex,
          record.limsResultData
        )
      );
    });

    this.hwrSocket.on('task', (record, callback) => {
      if (callback) {
        callback();
      }

      // The current node might not be a task, in that case ignore it
      if (
        store.getState().queueGUI.displayData[record.queueID] &&
        record.taskIndex !== null
      ) {
        const taskCollapsed =
          store.getState().queueGUI.displayData[record.queueID].collapsed;

        if (record.state === 1 && !taskCollapsed) {
          this.dispatch(collapseItem(record.queueID));
        } else if (record.state >= 2 && taskCollapsed) {
          this.dispatch(collapseItem(record.queueID));
        }

        this.dispatch(
          addTaskResultAction(
            record.sample,
            record.taskIndex,
            record.state,
            record.progress,
            record.limsResultData,
            record.queueID
          )
        );
      }
    });

    this.hwrSocket.on('add_task', (record, callback) => {
      if (callback) {
        callback();
      }

      this.dispatch(addTaskAction(record.tasks));
    });

    this.hwrSocket.on('add_diff_plan', (record, callback) => {
      if (callback) {
        callback();
      }
      this.dispatch(addDiffractionPlanAction(record.tasks));
    });

    this.hwrSocket.on('queue', (record, callback) => {
      if (callback) {
        callback();
      }

      if (record.Signal === 'DisableSample') {
        this.dispatch(setSampleAttribute(record.sampleID, 'checked', false));
      } else {
        this.dispatch(setStatus(record.Signal));
      }
    });

    this.hwrSocket.on('sc', (record) => {
      switch (record.signal) {
      case 'operatingSampleChanger': {
        this.dispatch(
          setLoading(
            true,
            'Sample changer in operation',
            record.message,
            true,
            () => this.dispatch(sendStopQueue())
          )
        );
      
      break;
      }
      case 'loadingSample': 
      case 'loadedSample': {
        this.dispatch(
          setLoading(
            true,
            `Loading sample ${record.location}`,
            record.message,
            true,
            () => this.dispatch(sendStopQueue())
          )
        );
      
      break;
      }
      case 'unLoadingSample': 
      case 'unLoadedSample': {
        this.dispatch(
          setLoading(
            true,
            `Unloading sample ${record.location}`,
            record.message,
            true,
            () => this.dispatch(sendStopQueue())
          )
        );
      
      break;
      }
      case 'loadReady': {
        this.dispatch(
          setLoading(false, 'SC Ready', record.message, true, () =>
            this.dispatch(sendStopQueue())
          )
        );
      
      break;
      }
      case 'inSafeArea': {
        this.dispatch(
          setLoading(false, 'SC Safe', record.message, true, () =>
            this.dispatch(sendStopQueue())
          )
        );
      
      break;
      }
      // No default
      }
    });

    this.hwrSocket.on('sample_centring', (data) => {
      if (data.method === CLICK_CENTRING) {
        this.dispatch(startClickCentring());
        const msg =
          '3-Click Centring: <br /> Select centered position or center';
        this.dispatch(videoMessageOverlay(true, msg));
      } else {
        const msg = 'Auto loop centring: <br /> Save position or re-center';
        this.dispatch(videoMessageOverlay(true, msg));
      }
    });

    this.hwrSocket.on('disconnect', () => {
      if (this.connected) {
        this.connected = false;
        setTimeout(() => {
          this.dispatch(showConnectionLostDialog(!this.connected));
        }, 2000);
      }
    });

    this.hwrSocket.on('connect', () => {
      this.connected = true;
      this.dispatch(showConnectionLostDialog(false));
    });

    this.hwrSocket.on('resumeQueueDialog', () => {
      this.dispatch(showResumeQueueDialog(true));
    });

    this.hwrSocket.on('observersChanged', (data) => {
      const state = store.getState();

      if (
        data.observers.length > 0 &&
        data.operator.username === state.login.user.username &&
        !state.login.user.inControl
      ) {
        this.dispatch(setLoading(true, 'You were given control', data.message));
      } else if (
        data.observers.length > 0 &&
        state.login.user.inControl &&
        data.observers
          .map((el) => el.username)
          .includes(state.login.user.username)
      ) {
        this.dispatch(setLoading(true, 'You lost control', 'You lost control'));
      }

      this.dispatch(getRaState());
      this.dispatch(getLoginInfo());
    });

    this.hwrSocket.on('observerLogout', (observer) => {
      addResponseMessage(
        `**${observer.nickname}** (${observer.ip}) disconnected.`
      );
    });

    this.hwrSocket.on('observerLogin', (observer) => {
      if (observer.nickname && observer.ip) {
        addResponseMessage(
          `**${observer.nickname}** (${observer.ip}) connected.`
        );
      } else {
        addResponseMessage(`${observer.nickname} connecting ...`);
      }
    });

    this.hwrSocket.on('forceSignoutObservers', () => {
      const state = store.getState();

      if (!state.login.user.inControl) {
        this.dispatch(doSignOut());
      }
    });

    this.hwrSocket.on('workflowParametersDialog', (data) => {
      this.dispatch(showWorkflowParametersDialog(data));
    });

    this.hwrSocket.on('take_xtal_snapshot', (cb) => {
      cb(window.takeSnapshot());
    });

    this.hwrSocket.on('beamline_action', (data) => {
      this.dispatch(setActionState(data.name, data.state, data.data));
    });

    this.hwrSocket.on('sc_state', (state) => {
      this.dispatch(setSCState(state));
    });

    this.hwrSocket.on('loaded_sample_changed', (data) => {
      this.dispatch(setLoadedSample(data));
    });

    this.hwrSocket.on('set_current_sample', (sample) => {
      this.dispatch(setCurrentSample(sample.sampleID));
    });

    this.hwrSocket.on('sc_maintenance_update', (data) => {
      this.dispatch(setSCGlobalState(data));
    });

    this.hwrSocket.on('sc_contents_update', () => {
      this.dispatch(updateSCContents());
    });

    this.hwrSocket.on('diff_phase_changed', (data) => {
      this.dispatch(setCurrentPhase(data.phase));
    });

    this.hwrSocket.on('new_plot', (plotInfo) => {
      this.dispatch(newPlot(plotInfo));
    });

    this.hwrSocket.on('plot_data', (data) => {
      this.dispatch(plotData(data.id, data.data, false));
    });

    this.hwrSocket.on('plot_end', (data) => {
      this.dispatch(plotData(data.id, data.data, true));
      this.dispatch(plotEnd(data));
    });
  }
}

export const serverIO = new ServerIO();
