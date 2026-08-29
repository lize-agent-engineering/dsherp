import React from 'react';
import {createRoot} from 'react-dom/client';
import AgentWorkbench from './AgentWorkbench.jsx';
import {contextApi} from './context-api.js';

export function readWorkbenchState(search=globalThis.location?.search||''){
 const params=new URLSearchParams(search);
 const initialSession=params.get('session')||null;
 const token=params.get('handoff');
 let handoff=null;
 if(token&&/^[a-f0-9]{32,64}$/i.test(token)){
   const key=`dsherp-agent-handoff:${token}`;
   const raw=sessionStorage.getItem(key);
   sessionStorage.removeItem(key);
   if(raw){try{handoff=JSON.parse(raw);}catch{handoff=null;}}
 }
 return {initialSession,handoff};
}

export function mount(element){
 const root=createRoot(element);
 root.render(React.createElement(AgentWorkbench,{api:contextApi,...readWorkbenchState()}));
 return()=>root.unmount();
}
