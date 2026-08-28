// Presentation only: the original immutable proposal remains the confirmation.
export function proposalRows(changes){
 return changes.flatMap(change=>{
  if(!Array.isArray(change.after))return [change];
  const provided=new Map((change.form_before??[]).map(row=>[row.name,row]));
  const before=(change.before??[]).map(row=>({...row,...provided.get(row.name)}));
  const existing=new Map(before.map((row,index)=>[row.name,{row,index}]));
  const retained=new Set(change.after.map(row=>row.name).filter(Boolean));
  const rows=[];
  const add=(key,label,old,value)=>rows.push({field:change.field+'.'+key,label:change.label+' / '+label,before:old,after:value});
  change.after.forEach((row,index)=>{
   const old=existing.get(row.name);
   const label=old?(old.row.item_code||row.name):`新增第${index+1}行`;
   if(old&&old.index!==index)add(index+'.position',label+' / 行位置',old.index+1,index+1);
   for(const [field,value] of Object.entries(row)){
    if(field==='name')continue;
    if(old&&JSON.stringify(old.row[field])===JSON.stringify(value))continue;
    add(index+'.'+field,label+' / '+(change.columns?.[field]||field),old?.row[field]??null,value);
   }
  });
  before.forEach((row,index)=>{
   if(retained.has(row.name))return;
   for(const [field,value] of Object.entries(row))if(field!=='name'){
    add('removed'+index+'.'+field,`删除第${index+1}行 / `+(change.columns?.[field]||field),value,null);
   }
  });
  return rows;
 });
}
