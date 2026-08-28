import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
export function mount(element) {
  const root = createRoot(element);
  root.render(<App />);
  return () => root.unmount();
}

import Portal from './Portal.jsx';
async function platformApi(method,params={},signal){
 const response=await fetch('/api/method/dsherp_platform.api.'+method+'?'+new URLSearchParams(params),{credentials:'same-origin',signal});
 if(!response.ok){
  if(response.status===401||response.status===403)throw new Error('当前身份或企业成员权限不足，请检查登录和成员授权。');
  throw new Error(`业务请求未完成（HTTP ${response.status}），请检查记录编号与服务状态。`);
 }
 return (await response.json()).message;
}
export function mountPortal(element){
 const root=createRoot(element);root.render(<Portal api={platformApi}/>);return()=>root.unmount();
}
