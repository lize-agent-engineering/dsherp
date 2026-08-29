import React,{useEffect,useState} from 'react';
import {Alert,Spin,Typography} from 'antd';

export default function PreviewTransfer({transferId,api}){
 const [state,setState]=useState({loading:true,error:''});
 useEffect(()=>{
  let active=true;
  api('accept_configuration_transfer',{transfer_id:transferId})
   .then(result=>{if(active)setState({loading:false,error:'',result});})
   .catch(error=>{if(active)setState({loading:false,error:error.message});});
  return()=>{active=false;};
 },[api,transferId]);
 if(state.loading)return <Spin tip="正在核实源站并接收配置包"/>;
 if(state.error)return <Alert type="error" message={state.error} description="没有应用任何配置。请核实身份和源站权限后重新打开交接入口。"/>;
 return <Alert type="success" message="配置包已接收到隔离站点"
   description={<Typography.Text>请打开右下角 Agent，查看具体差异并生成预览确认。</Typography.Text>}/>;
}
