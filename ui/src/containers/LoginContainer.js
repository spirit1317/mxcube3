import React, { Component } from 'react';
import { bindActionCreators } from 'redux';
import { connect } from 'react-redux';
import {
  signIn,
  doSignOut,
  selectProposal,
  sendSelectProposal,
  hideProposalsForm,
} from '../actions/login';
import { setLoading } from '../actions/general';
import Login from '../components/Login/Login';

class LoginContainer extends Component {
  render() {
    return <Login {...this.props} />;
  }
}

function mapStateToProps(state) {
  return {
    loading: state.general.loading,
    showError: state.general.showErrorPanel,
    showProposalsForm: state.login.showProposalsForm,
    data: state.login,
    selectedProposal: state.login.selectedProposal,
  };
}

function mapDispatchToProps(dispatch) {
  return {
    signIn: bindActionCreators(signIn, dispatch),
    doSignOut: bindActionCreators(doSignOut, dispatch),
    setLoading: bindActionCreators(setLoading, dispatch),
    selectProposal: bindActionCreators(selectProposal, dispatch),
    sendSelectProposal: bindActionCreators(sendSelectProposal, dispatch),
    hideProposalsForm: bindActionCreators(hideProposalsForm, dispatch),
  };
}

export default connect(mapStateToProps, mapDispatchToProps)(LoginContainer);
