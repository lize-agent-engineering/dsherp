import React from 'react';
import {Button,Result} from 'antd';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {hasError: false};
  }
  static getDerivedStateFromError() {
    return {hasError: true};
  }
  componentDidCatch(error, info) {
    console.error(error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <Result
          status="error"
          title="助手暂不可用，请重新加载"
          extra={<Button onClick={() => globalThis.location.reload()}>重新加载</Button>}
        />
      );
    }
    return this.props.children;
  }
}
