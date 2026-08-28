import {mountDeskContext} from './desk-context.jsx';
if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',mountDeskContext,{once:true});
}else{
  mountDeskContext();
}
