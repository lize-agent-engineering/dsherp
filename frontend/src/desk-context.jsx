import React from 'react';
import {createRoot} from 'react-dom/client';
import ContextSidebar from './ContextSidebar.jsx';
import {contextApi} from './context-api.js';

export function mountDeskContext(){
  if(document.getElementById('dsherp-context-root'))return;
  const element=document.createElement('div');
  element.id='dsherp-context-root';
  document.body.append(element);
  createRoot(element).render(<ContextSidebar api={contextApi}/>);
}
