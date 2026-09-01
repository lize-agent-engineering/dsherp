import React from 'react';
import {createRoot} from 'react-dom/client';
import ContextSidebar from './ContextSidebar.jsx';
import PreviewTransfer from './PreviewTransfer.jsx';
import {contextApi} from './context-api.js';

export function mountDeskContext(){
  if(location.pathname==='/desk/dsherp-agent')return;
  if(document.getElementById('dsherp-context-root'))return;
  const element=document.createElement('div');
  element.id='dsherp-context-root';
  document.body.append(element);
  createRoot(element).render(<ContextSidebar api={contextApi}/>);
}

export function mountPreviewTransfer(element,transferId){
  const root=createRoot(element);root.render(<PreviewTransfer transferId={transferId} api={contextApi}/>);
  return()=>root.unmount();
}
