import {mountDeskContext,mountPreviewTransfer} from './desk-context.jsx';
window.dsherpContext={mountPreviewTransfer};
if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',mountDeskContext,{once:true});
}else{
  mountDeskContext();
}
