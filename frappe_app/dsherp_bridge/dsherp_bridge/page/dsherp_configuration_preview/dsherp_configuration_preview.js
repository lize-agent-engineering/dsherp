frappe.pages['dsherp-configuration-preview'].on_page_load=function(wrapper){
 const page=frappe.ui.make_app_page({parent:wrapper,title:'隔离配置预览',single_column:true});
 const root=document.createElement('div');page.body.append(root);
 const transferId=frappe.get_route()[1];
 if(!transferId){root.textContent='缺少配置交接标识。';return;}
 if(!window.dsherpContext?.mountPreviewTransfer){root.textContent='配置预览组件尚未加载，请刷新页面。';return;}
 let dispose=null;
 wrapper.on_page_show=function(){if(!dispose)dispose=window.dsherpContext.mountPreviewTransfer(root,transferId);};
 $(wrapper).on('hide',()=>{if(dispose)dispose();dispose=null;});
};
