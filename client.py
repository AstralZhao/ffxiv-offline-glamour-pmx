"""Native wardrobe client: Tk controls and a directly presented D3D11 HWND."""
from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
from PIL import Image

from app import DemoEngine,GAME,DEFAULT_PRESET,ROOT,SLOTS,equipment_rows,parse_state


def enable_legacy_tk():
    # Shared controller helpers used by Qt do not require the legacy Tk GUI.
    global tk,ttk,filedialog,messagebox,simpledialog,ImageTk
    import tkinter as tk
    from tkinter import ttk,filedialog,messagebox,simpledialog
    from PIL import ImageTk


def color_text(color):
    rgb=tuple(int(color[i:i+2],16)for i in(1,3,5))
    return '#10141b'if sum(a*b for a,b in zip(rgb,(.299,.587,.114)))>155 else'white'


def merge_pending_state(result,baseline,current):
    """Retain edits made during a model operation over its completed result.

    Exposure/resize events alone are not state edits. An equipment choice
    travels with its dyes so a late tint cannot target a different item.
    """
    merged=deepcopy(result)
    for key in result:
        if key=='equipment':
            for slot,choice in current[key].items():
                if choice!=baseline[key][slot]:merged[key][slot]=deepcopy(choice)
        elif current[key]!=baseline[key]:merged[key]=deepcopy(current[key])
    return merged


class NativeClient:
    def __init__(self,root,game=GAME,preset=DEFAULT_PRESET,*,preset_directory=None,session_path=None):
        enable_legacy_tk()
        self.root=root;self.closed=False;self.closing=False;self.job=None;self.dirty=False;self.timer=None;self.generation=0
        self.session_timer=None;self.accept_baseline=None
        self.boot=None;self.ready=False;self.state=parse_state({});self.good=deepcopy(self.state)
        self.items={};self.icons=OrderedDict();self.rows=[];self.folder=None;self.outer=None;self.offset=0;self.more=False
        self.editor=None;self.editor_record=None;self.draft=None;self.drag=None;self.search_timer=None
        self.session_path=Path(session_path)if session_path else ROOT/'artifacts/demo/native-session.json'
        self.message=tk.StringVar(value='正在准备国服资源与预设…');self.slot=tk.StringVar(value=SLOTS['Head'])
        self.query=tk.StringVar();self.preset_name=tk.StringVar();self.folder_name=tk.StringVar(value='全部同模目录')
        self.appearance_note=tk.StringVar();self.selected_note=tk.StringVar(value='未装备')
        self.show_head=tk.BooleanVar(value=True)
        root.title('幻衣间 · D3D11 客户端');root.geometry('1440x920');root.minsize(1100,720)
        style=ttk.Style(root);style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10),background='#20242e',foreground='#edf0f6')
        style.configure('TFrame',background='#20242e');style.configure('TLabelframe',background='#20242e')
        style.configure('TLabelframe.Label',foreground='#87dfcf');style.configure('TButton',padding=(8,6))
        style.configure('TEntry',fieldbackground='#303644',foreground='#edf0f6')
        style.configure('TCombobox',fieldbackground='#303644',foreground='#edf0f6',arrowsize=16)
        style.map('TCombobox',fieldbackground=[('readonly','#303644')],foreground=[('readonly','#edf0f6')])
        style.configure('Treeview',background='#171b24',fieldbackground='#171b24',foreground='#edf0f6',rowheight=46,borderwidth=0)
        style.map('Treeview',background=[('selected','#345852')]);style.configure('Treeview.Heading',background='#303644')
        self.build_ui();root.update_idletasks()
        self.engine=DemoEngine(Path(game),Path(preset),preset_directory=preset_directory,native_hwnd=self.viewport.winfo_id())
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.after(50,self.poll_startup)

    def build_ui(self):
        shell=ttk.Frame(self.root,padding=12);shell.pack(fill='both',expand=True)
        top=ttk.Frame(shell);top.pack(fill='x',pady=(0,10))
        ttk.Label(top,text='幻衣间',font=('Microsoft YaHei UI',20,'bold')).pack(side='left')
        ttk.Label(top,text='  国服本地装备 · 原生三维试衣',foreground='#95a1b5').pack(side='left')
        ttk.Button(top,text='我的搭配',command=self.saved_dialog).pack(side='right')
        ttk.Button(top,text='收藏当前搭配',command=self.save_outfit).pack(side='right',padx=8)
        panes=ttk.Panedwindow(shell,orient='horizontal');panes.pack(fill='both',expand=True)
        left=ttk.Frame(panes,width=300,padding=(0,0,10,0));center=ttk.Frame(panes);right=ttk.Frame(panes,width=350,padding=(10,0,0,0))
        panes.add(left,weight=0);panes.add(center,weight=1);panes.add(right,weight=0)
        ttk.Label(left,text='角色预设',font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=6)
        self.preset_combo=ttk.Combobox(left,textvariable=self.preset_name,state='readonly',width=27);self.preset_combo.pack(fill='x')
        self.preset_combo.bind('<<ComboboxSelected>>',self.select_preset)
        actions=ttk.Frame(left);actions.pack(fill='x',pady=8)
        ttk.Button(actions,text='导入预设',command=self.import_preset).pack(side='left')
        ttk.Button(actions,text='编辑发型与捏脸',command=self.open_editor).pack(side='right')
        ttk.Label(left,textvariable=self.appearance_note,foreground='#e5c481',wraplength=280).pack(fill='x')
        ttk.Label(left,text='当前搭配',font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=(18,8))
        self.outfit=ttk.Treeview(left,columns=('name',),show='tree headings',height=10,selectmode='browse')
        self.outfit.heading('#0',text='部位');self.outfit.column('#0',width=60,stretch=False)
        self.outfit.heading('name',text='装备');self.outfit.column('name',width=195);self.outfit.pack(fill='x')
        for slot,name in SLOTS.items():self.outfit.insert('','end',iid=slot,text=name,values=('未装备',))
        self.outfit.selection_set('Head');self.outfit.bind('<<TreeviewSelect>>',self.outfit_selected)
        ttk.Label(left,textvariable=self.selected_note,wraplength=280).pack(fill='x',pady=(10,6))
        colors=ttk.Frame(left);colors.pack(fill='x')
        self.dye_buttons=[]
        for channel in(1,2):
            button=tk.Button(colors,text=f'第{channel}区 · 原色',command=lambda ch=channel:self.dye_dialog(ch),bg='#333b48',fg='white',relief='flat',padx=8,pady=8)
            button.pack(side='left',fill='x',expand=True,padx=2);self.dye_buttons.append(button)
        ttk.Button(left,text='脱下当前部位',command=self.remove_slot).pack(fill='x',pady=(10,4))
        ttk.Button(left,text='全部脱下（默认内衣）',command=self.clear_outfit).pack(fill='x')
        ttk.Checkbutton(left,text='显示头饰',variable=self.show_head,command=self.toggle_head).pack(anchor='w',pady=10)
        ttk.Label(left,text='武器不包含在内。附加粒子特效暂不显示。',foreground='#95a1b5',wraplength=280).pack(anchor='w',pady=8)
        self.viewport=tk.Frame(center,bg='#0b0d13',width=640,height=760,takefocus=True);self.viewport.pack(fill='both',expand=True)
        self.viewport.bind('<Configure>',lambda e:self.queue_render());self.viewport.bind('<Expose>',lambda e:self.queue_render())
        self.viewport.bind('<ButtonPress-1>',lambda e:self.begin_drag(e,1));self.viewport.bind('<ButtonPress-3>',lambda e:self.begin_drag(e,3))
        self.viewport.bind('<B1-Motion>',self.move_camera);self.viewport.bind('<B3-Motion>',self.move_camera)
        self.viewport.bind('<ButtonRelease-1>',self.end_drag);self.viewport.bind('<ButtonRelease-3>',self.end_drag)
        self.viewport.bind('<MouseWheel>',lambda e:self.zoom(.1 if e.delta>0 else -.1))
        toolbar=ttk.Frame(center,padding=(0,8));toolbar.pack(fill='x')
        for label,yaw in(('正面',0),('侧面',90),('背面',180)):
            ttk.Button(toolbar,text=label,command=lambda y=yaw:self.camera_yaw(y)).pack(side='left',padx=2)
        ttk.Button(toolbar,text='−',command=lambda:self.zoom(-.15)).pack(side='left',padx=(12,2))
        ttk.Button(toolbar,text='+',command=lambda:self.zoom(.15)).pack(side='left',padx=2)
        ttk.Button(toolbar,text='还原相机',command=self.reset_camera).pack(side='right')
        ttk.Label(center,text='左键旋转 / 俯仰 · 右键上下平移 · 滚轮缩放',foreground='#95a1b5').pack()
        ttk.Label(right,text='装备目录',font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=6)
        slots=ttk.Combobox(right,textvariable=self.slot,values=list(SLOTS.values()),state='readonly');slots.pack(fill='x')
        slots.bind('<<ComboboxSelected>>',lambda e:self.select_slot(self.slot_key()))
        search=ttk.Entry(right,textvariable=self.query);search.pack(fill='x',pady=8)
        self.query.trace_add('write',lambda *_:self.schedule_search())
        nav=ttk.Frame(right);nav.pack(fill='x',pady=(0,8))
        self.back_button=ttk.Button(nav,text='‹ 返回',command=self.back_folder);self.back_button.pack(side='left')
        ttk.Label(nav,textvariable=self.folder_name,wraplength=245).pack(side='left',padx=6)
        self.catalog=ttk.Treeview(right,columns=('dyes',),show='tree headings',selectmode='browse',height=12)
        self.catalog.heading('#0',text='装备 / 同模目录');self.catalog.column('#0',width=265)
        self.catalog.heading('dyes',text='染区');self.catalog.column('dyes',width=45,stretch=False)
        scrollbar=ttk.Scrollbar(right,orient='vertical',command=self.catalog.yview);self.catalog.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y');self.catalog.pack(fill='both',expand=True)
        self.catalog.bind('<Double-1>',self.catalog_activate);self.catalog.bind('<Return>',self.catalog_activate)
        ttk.Button(right,text='加载更多',command=lambda:self.load_catalog(True)).pack(fill='x',pady=6)
        self.count_note=ttk.Label(right,text='双击同模目录，再双击款式试穿',foreground='#95a1b5');self.count_note.pack(anchor='w')
        ttk.Label(shell,textvariable=self.message,wraplength=1300).pack(fill='x',pady=(10,0))

    def poll_startup(self):
        if self.closing:return self.close()
        if not self.engine.ready.done():self.root.after(80,self.poll_startup);return
        if self.engine.status!='ready':
            self.message.set('初始角色未能加载：'+str(self.engine.error));self.refresh_boot();return
        session=None
        if not getattr(self.engine,'startup_session_prepared',False)and self.session_path.is_file():
            try:session=json.loads(self.session_path.read_bytes())
            except(ValueError,OSError):session=None
        # Read the prior session before accepting the initial frame; accepting
        # a frame normally persists it and must not overwrite the restore input.
        self.ready=True;self.refresh_boot();self.accept_result(self.engine.last,persist=False);self.load_catalog()
        if session and session.get('preset_id')in self.engine.presets:
            def restored(result):self.accept_result(result);self.queue_render()
            self.submit(self.restore_session,restored,session);return
        self.persist_session()
        self.queue_render()

    def restore_session(self,record):
        key=record['preset_id'];self.engine.run('preset',{'id':key})
        current=self.engine.describe_appearance(key)
        rendered=record.get('rendered_parameters')
        if rendered and record.get('saved_preset_sha256')==current['sha256']and rendered!=current['parameters']:
            self.engine.run('appearance_preview',{'id':key,'sha256':current['sha256'],
                'changes':{k:v for k,v in rendered.items()if v!=current['parameters'][k]}})
        return self.engine.render(record['state'])

    def refresh_boot(self):
        self.boot=self.engine.bootstrap();self.preset_ids=[p['id']for p in self.boot['presets']]
        self.preset_combo['values']=[p['name']+' · '+p['file_name']for p in self.boot['presets']]
        if self.boot['preset_id']in self.preset_ids:self.preset_combo.current(self.preset_ids.index(self.boot['preset_id']))
        self.appearance_note.set('当前模型与已保存预设不同'if not self.boot['appearance']['model_matches_file']else'软件预设库 · 游戏文件只读')

    def submit(self,fn,callback,*arguments):
        if self.job is not None or self.closing:
            if not self.closing:self.message.set('请等待当前模型准备完成')
            return False
        generation=self.generation;self.message.set('正在准备…')
        self.job=(self.engine.pool.submit(fn,*arguments),callback,generation,deepcopy(self.state))
        self.root.after(10,self.poll_job);return True

    def poll_job(self):
        if self.job is None:return
        future,callback,generation,baseline=self.job
        if not future.done():self.root.after(10,self.poll_job);return
        self.job=None
        self.accept_baseline=baseline
        try:callback(future.result())
        except Exception as error:
            if self.generation==generation:self.state=deepcopy(self.good)
            self.message.set('未完成本次操作，保留上次模型。'+str(error));self.refresh_boot();self.update_outfit()
        finally:self.accept_baseline=None
        if self.closing:return self.close()
        if self.dirty:self.pump_render()

    def queue_render(self):
        if not self.ready or self.closing:return
        self.generation+=1;self.dirty=True
        if self.timer is None:self.timer=self.root.after(16,self.pump_render)

    def pump_render(self):
        if self.timer is not None:self.root.after_cancel(self.timer);self.timer=None
        if not self.ready or self.closing or self.job is not None or not self.dirty:return
        self.dirty=False;state=deepcopy(self.state);generation=self.generation
        size=(max(64,self.viewport.winfo_width()),max(64,self.viewport.winfo_height()))
        effect_frame=self.render_effect_frame()
        def draw():
            self.engine.renderer.resize_native(*size)
            return self.engine.render(state,effect_frame=effect_frame)if effect_frame is not None else self.engine.render(state)
        def done(result):
            self.good=deepcopy(result['state'])
            if generation==self.generation:self.state=deepcopy(self.good)
            self.accept_result(result,replace_state=False,refresh=False)
        self.submit(draw,done)

    def render_effect_frame(self):return None

    def effect_frame_presented(self,result):pass

    def accept_result(self,result,replace_state=True,*,persist=True,refresh=True):
        self.effect_frame_presented(result)
        was_ready=self.ready
        if replace_state:
            self.good=deepcopy(result['state'])
            self.state=merge_pending_state(self.good,self.accept_baseline,self.state)if self.accept_baseline is not None else deepcopy(self.good)
            if self.state!=self.good:self.dirty=True
        self.ready=True
        if refresh:self.refresh_boot()
        self.update_outfit()
        if not was_ready:self.load_catalog()
        self.message.set('D3D11 直接显示 · '+str(result['visible_meshes'])+' 个网格 · '+f"{result['seconds']*1000:.0f} 毫秒 / 帧"+
            (' · '+' '.join(result['notices'])if result.get('notices')else''))
        if persist:self.schedule_session_save()

    def schedule_session_save(self):
        if self.session_timer is None:self.session_timer=self.root.after(500,self.flush_session)

    def flush_session(self):
        if self.session_timer is not None:self.root.after_cancel(self.session_timer);self.session_timer=None
        if self.job is not None:
            self.session_timer=self.root.after(100,self.flush_session);return
        try:self.persist_session()
        except OSError as error:self.message.set('模型已显示，但会话记录未能保存：'+str(error))

    def persist_session(self):
        if not self.engine.last:return
        p=self.engine.presets.get(self.engine.active_preset)
        record={'preset_id':self.engine.active_preset,'state':self.good,'rendered_parameters':self.engine.rendered_parameters,
            'saved_preset_sha256':p[1]['sha256']if p else None}
        self.session_path.parent.mkdir(parents=True,exist_ok=True);temp=self.session_path.with_suffix('.tmp')
        temp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8');temp.replace(self.session_path)

    def slot_key(self):return next(s for s,n in SLOTS.items()if n==self.slot.get())

    def update_outfit(self):
        for slot,choice in self.state['equipment'].items():
            item=self.items.get(choice['item_id'])
            if choice['item_id']and item is None:
                result=equipment_rows(slot,str(choice['item_id']));self.items.update({r['item_id']:r for r in result['items']});item=self.items.get(choice['item_id'])
            self.outfit.item(slot,values=((item or{}).get('name','未装备'),))
        self.show_head.set(self.state['head_visible']);slot=self.slot_key();choice=self.state['equipment'][slot];item=self.items.get(choice['item_id'],{})
        self.selected_note.set(SLOTS[slot]+' · '+item.get('name','未装备'))
        for channel,button in enumerate(self.dye_buttons,1):
            dye=next((d for d in self.boot['dyes']if d['stain_id']==choice['first'if channel==1 else'second']),None)
            enabled=item.get('dye_count',0)>=channel
            color='#%06x'%(dye['color']&0xffffff)if dye and dye['stain_id']else'#333b48'
            button.configure(text=f"{channel}区 · "+(dye['name']if dye and dye['stain_id']else'原色'),bg=color,fg=color_text(color),state='normal'if enabled else'disabled')

    def select_slot(self,slot):
        if not self.boot:return
        self.slot.set(SLOTS[slot]);self.outfit.selection_set(slot);self.folder=None;self.outer=None
        self.cancel_search();self.query.set('');self.cancel_search();self.update_outfit();self.load_catalog()

    def outfit_selected(self,event=None):
        selected=self.outfit.selection()
        if selected and selected[0]!=self.slot_key():self.select_slot(selected[0])

    def cancel_search(self):
        if self.search_timer is not None:self.root.after_cancel(self.search_timer);self.search_timer=None

    def schedule_search(self):
        self.cancel_search();self.search_timer=self.root.after(230,self.load_catalog)

    def icon(self,identifier):
        if not identifier:return ''
        if identifier in self.icons:self.icons.move_to_end(identifier);return self.icons[identifier]
        try:
            image=Image.open(BytesIO(self.engine.item_icons.png(identifier))).convert('RGBA');image.thumbnail((32,32))
            value=ImageTk.PhotoImage(image,master=self.root)
        except(FileNotFoundError,ValueError,NotImplementedError):return ''
        self.icons[identifier]=value
        # Keep references for every loaded list row; clearing the list frees it.
        return value

    def load_catalog(self,append=False):
        self.cancel_search()
        if not self.boot:return
        if append and not self.more:return
        if not append:self.offset=0;self.rows=[];self.catalog.delete(*self.catalog.get_children());self.icons.clear()
        result=equipment_rows(self.slot_key(),self.query.get(),self.offset,grouped=self.folder is None,model_set=self.folder)
        self.items.update({r['item_id']:r for r in result['items']});self.rows.extend(result['items']);self.offset+=len(result['items']);self.more=result['more']
        for row in result['items']:
            iid=('g'if self.folder is None else'i')+str(row['model_set']if self.folder is None else row['item_id'])
            label=row['name']+(f"  同模 · {row['group_count']}款"if self.folder is None else'')
            self.catalog.insert('','end',iid=iid,text=label,image=self.icon(row.get('icon_id')),values=(row['dye_count'],))
        self.folder_name.set('全部同模目录'if self.folder is None else next((r['name']for r in self.outer['rows']if r['model_set']==self.folder),'同模款式'))
        self.back_button.configure(state='disabled'if self.folder is None else'normal')
        self.count_note.configure(text=f"{result['total']:,} "+('个同模目录'if self.folder is None else'款装备')+' · 双击选择')
        self.update_outfit()

    def catalog_activate(self,event=None):
        selected=self.catalog.selection()
        if not selected or not self.ready:return
        iid=selected[0]
        if iid.startswith('g'):
            self.outer={'query':self.query.get(),'rows':list(self.rows),'offset':self.offset,'more':self.more,'scroll':self.catalog.yview(),
                'selection':iid,'count':self.count_note['text']}
            self.folder=int(iid[1:]);self.query.set('');self.load_catalog();return
        self.state['equipment'][self.slot_key()]={'item_id':int(iid[1:]),'first':0,'second':0};self.update_outfit();self.queue_render()

    def back_folder(self):
        if self.folder is None or not self.outer:return
        saved=self.outer;self.folder=None;self.outer=None;self.query.set(saved['query']);self.cancel_search()
        self.rows=saved['rows'];self.offset=saved['offset'];self.more=saved['more'];self.catalog.delete(*self.catalog.get_children());self.icons.clear()
        for row in self.rows:self.catalog.insert('','end',iid='g'+str(row['model_set']),text=f"{row['name']}  同模 · {row['group_count']}款",image=self.icon(row.get('icon_id')),values=(row['dye_count'],))
        self.catalog.selection_set(saved['selection']);self.catalog.yview_moveto(saved['scroll'][0])
        self.folder_name.set('全部同模目录');self.back_button.configure(state='disabled');self.count_note.configure(text=saved['count'])

    def begin_drag(self,event,button):
        if self.ready:self.drag=(event.x,event.y,self.state['yaw'],self.state['pitch'],self.state['pan'],button)

    def move_camera(self,event):
        if self.drag is None:return
        x,y,yaw,pitch,pan,button=self.drag
        if button==3:self.state['pan']=max(-1,min(1,pan+(event.y-y)*.0015))
        else:self.state['yaw']=(yaw+(event.x-x)*.6)%360;self.state['pitch']=max(-65,min(65,pitch+(event.y-y)*.3))
        self.queue_render()

    def end_drag(self,event=None):self.drag=None
    def camera_yaw(self,yaw):self.state['yaw']=yaw;self.queue_render()
    def zoom(self,delta):self.state['zoom']=max(.25,min(4,self.state['zoom']+delta));self.queue_render()
    def reset_camera(self):self.state.update(yaw=0,pitch=0,pan=0,zoom=1);self.queue_render()
    def toggle_head(self):self.state['head_visible']=self.show_head.get();self.queue_render()
    def remove_slot(self):self.state['equipment'][self.slot_key()]={'item_id':None,'first':0,'second':0};self.update_outfit();self.queue_render()
    def clear_outfit(self):
        for slot in SLOTS:self.state['equipment'][slot]={'item_id':None,'first':0,'second':0}
        self.update_outfit();self.queue_render()

    def palette(self,title,choices,selected,callback):
        dialog=tk.Toplevel(self.root);dialog.title(title);dialog.geometry('650x560');dialog.transient(self.root)
        text=tk.StringVar();ttk.Entry(dialog,textvariable=text).pack(fill='x',padx=12,pady=12)
        canvas=tk.Canvas(dialog,bg='#20242e',highlightthickness=0);bar=ttk.Scrollbar(dialog,command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set);bar.pack(side='right',fill='y');canvas.pack(fill='both',expand=True)
        grid=ttk.Frame(canvas);canvas.create_window(0,0,window=grid,anchor='nw')
        def render(*_):
            for child in grid.winfo_children():child.destroy()
            filtered=[c for c in choices if text.get()in c['label']]
            for i,choice in enumerate(filtered):
                def pick(c=choice):callback(c['value']);dialog.destroy()
                color=choice.get('color','#333b48')
                tk.Button(grid,text=('✓ 'if choice['value']==selected else'')+choice['label'],bg=color,
                    fg=color_text(color),width=9,height=2,relief='flat',command=pick,wraplength=80).grid(row=i//6,column=i%6,padx=3,pady=3)
            grid.update_idletasks();canvas.configure(scrollregion=canvas.bbox('all'))
        text.trace_add('write',render);render();return dialog

    def dye_dialog(self,channel):
        slot=self.slot_key();choice=self.state['equipment'][slot];item=self.items.get(choice['item_id'],{})
        if item.get('dye_count',0)<channel:return
        colors=[{'value':0,'label':'原色','color':'#333b48'}]+[{'value':d['stain_id'],'label':d['name'],'color':'#%06x'%(d['color']&0xffffff)}for d in self.boot['dyes']if d['stain_id']]
        def pick(value):
            if self.state['equipment'][slot]['item_id']!=choice['item_id']:return
            self.state['equipment'][slot]['first'if channel==1 else'second']=value;self.update_outfit();self.queue_render()
        self.palette(SLOTS[slot]+f' · 第{channel}染色区',colors,choice['first'if channel==1 else'second'],pick)

    def select_preset(self,event=None):
        if not self.boot:return
        index=self.preset_combo.current()
        if index>=0:self.submit(self.engine.run,self.accept_result,'preset',{'id':self.preset_ids[index]})

    def import_preset(self):
        if self.job:return self.message.set('请等待当前模型准备完成')
        name=filedialog.askopenfilename(parent=self.root,title='导入原生外观预设',filetypes=[('角色预设','*.dat')])
        if not name:return
        try:raw=Path(name).read_bytes()
        except OSError as error:return self.message.set(str(error))
        def done(result):
            self.refresh_boot()
            if result['model_loaded']:self.accept_result(result)
            else:
                self.message.set('预设文件已导入，模型未能加载：'+result['preview_error']);self.open_editor(result['preset']['id'])
        self.submit(self.engine.run,done,'import',{'name':Path(name).name,'data':base64.b64encode(raw).decode()})

    def open_editor(self,key=None):
        if not self.boot or self.job:return
        if self.editor and self.editor.winfo_exists():self.editor.lift();return
        key=key or self.boot['preset_id']
        def done(record):
            self.editor_record=record;self.draft=deepcopy(record['appearance']['rendered_parameters']if record['appearance']['is_preview']else record['parameters'])
            self.editor=tk.Toplevel(self.root);self.editor.title('编辑预设 · '+record['file_name']);self.editor.geometry('620x760');self.editor.transient(self.root)
            def close_editor():
                if self.job is not None:return
                self.editor.destroy();self.editor=None
            self.editor.protocol('WM_DELETE_WINDOW',close_editor)
            self.editor_status=tk.StringVar();ttk.Label(self.editor,textvariable=self.editor_status,wraplength=580).pack(fill='x',padx=12,pady=8)
            bar=ttk.Frame(self.editor,padding=12);bar.pack(side='bottom',fill='x')
            ttk.Button(bar,text='更新模型',command=lambda:self.submit_editor(True)).pack(side='left')
            ttk.Button(bar,text='保存预设',command=lambda:self.submit_editor(False)).pack(side='right')
            canvas=tk.Canvas(self.editor,bg='#20242e',highlightthickness=0);scroll=ttk.Scrollbar(self.editor,command=canvas.yview)
            canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(fill='both',expand=True)
            self.editor_form=ttk.Frame(canvas,padding=12);canvas.create_window(0,0,window=self.editor_form,anchor='nw',width=570)
            self.editor_form.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
            self.editor_fields()
        self.submit(self.engine.describe_appearance,done,key)

    def editor_fields(self):
        for child in self.editor_form.winfo_children():child.destroy()
        for i,option in enumerate(self.editor_record['fields']):
            field=option['field'];ttk.Label(self.editor_form,text=option['label']).grid(row=i,column=0,sticky='w',padx=4,pady=6)
            value=self.draft[field]&option['mask'];choices=list(option['choices'])
            if not any(c['value']==value for c in choices):choices.append({'value':value,'label':str(value)+'（当前预设）'})
            if option['kind']=='color':
                selected=next(c for c in choices if c['value']==value)
                def palette(o=option):
                    self.palette(o['label'],o['choices'],self.draft[o['field']],lambda v:self.editor_change(o,v))
                color=selected.get('color','#333b48')
                tk.Button(self.editor_form,text='颜色 '+str(value),bg=color,fg=color_text(color),width=30,command=palette).grid(row=i,column=1,sticky='ew',padx=8)
            else:
                combo=ttk.Combobox(self.editor_form,values=[c['label']for c in choices],state='readonly',width=30)
                combo.current(next(j for j,c in enumerate(choices)if c['value']==value));combo.grid(row=i,column=1,sticky='ew',padx=8)
                combo.bind('<<ComboboxSelected>>',lambda e,o=option,c=choices,w=combo:self.editor_change(o,c[w.current()]['value']))
        toggles=[('highlight_flags',128,'启用挑染'),('mouth',128,'启用唇色'),('eyes',128,'瞳孔形态选项'),('face_paint',128,'镜像彩绘')]
        if self.draft['race']==7:toggles=[t for t in toggles if t[:2]!=('mouth',128)]
        toggles += [('facial_features',1<<i,f'面部特征 {i+1}')for i in range(7)]
        for j,(field,bit,label)in enumerate(toggles,len(self.editor_record['fields'])):
            variable=tk.BooleanVar(value=bool(self.draft[field]&bit))
            def toggle(f=field,b=bit,v=variable):
                self.draft[f]=(self.draft[f]|b)if v.get()else(self.draft[f]&~b);self.editor_change_status()
            ttk.Checkbutton(self.editor_form,text=label,variable=variable,command=toggle).grid(row=j,column=0,columnspan=2,sticky='w',pady=3)
        self.editor_change_status()

    def editor_change(self,option,value):
        field=option['field'];self.draft[field]=(self.draft[field]&~option['mask'])|value
        if field=='tribe':
            def done(record):self.editor_record['fields']=record['fields'];self.editor_fields()
            self.submit(self.engine.describe_appearance,done,self.editor_record['id'],value)
        elif option['kind']=='color':self.editor_fields()
        self.editor_change_status()

    def editor_change_status(self):
        same_file=self.draft==self.editor_record['parameters'];same_model=self.draft==self.engine.rendered_parameters and self.engine.active_preset==self.editor_record['id']
        self.editor_status.set(('修改已保存'if same_file else'修改尚未保存')+' · '+('模型已更新'if same_model else'模型尚未更新'))

    def submit_editor(self,preview):
        if self.job:return
        record=self.editor_record;draft=deepcopy(self.draft)
        payload={'id':record['id'],'sha256':record['sha256'],'changes':{k:v for k,v in draft.items()if v!=record['parameters'][k]}}
        def done(result):
            self.refresh_boot()
            if preview:self.accept_result(result);self.editor.destroy();self.editor=None
            else:
                record['parameters']=deepcopy(result['preset']['parameters']);record['sha256']=result['preset']['sha256']
                self.editor_change_status();self.persist_session();self.message.set('已保存预设文件；模型保持原样')
        self.editor_status.set('正在更新模型…'if preview else'正在保存预设…')
        self.submit(self.engine.run,done,'appearance_preview'if preview else'appearance_save',payload)

    def save_outfit(self):
        if not self.ready or self.job:return
        name=simpledialog.askstring('收藏搭配','搭配名称',parent=self.root)
        if name:
            try:self.engine.save({'name':name,'state':self.good});self.message.set('搭配已收藏')
            except Exception as error:self.message.set(str(error))

    def saved_dialog(self):
        if not self.boot:return
        rows=self.engine.saved();dialog=tk.Toplevel(self.root);dialog.title('我的搭配');dialog.geometry('440x480')
        for row in rows:
            def choose(r=row):
                if self.job:return
                dialog.destroy()
                def load():
                    if self.engine.active_preset!=r['preset_id']:self.engine.run('preset',{'id':r['preset_id']})
                    return self.engine.render(r['state'])
                self.submit(load,self.accept_result)
            ttk.Button(dialog,text=row['name']+' · '+row['time'],command=choose).pack(fill='x',padx=12,pady=5)
        if not rows:ttk.Label(dialog,text='还没有收藏的搭配').pack(pady=30)

    def close(self):
        if self.closed:return
        self.closing=True;self.ready=False
        if self.timer is not None:self.root.after_cancel(self.timer);self.timer=None
        if not self.engine.ready.done()or self.job is not None:
            self.message.set('正在结束当前绘制，请稍候…');self.root.after(50,self.close);return
        if not hasattr(self,'close_future'):
            self.close_future=self.engine.pool.submit(self.engine.close_gpu);self.root.after(30,self.close);return
        if not self.close_future.done():self.root.after(30,self.close);return
        try:self.close_future.result()
        finally:
            self.flush_session();self.engine.pool.shutdown(wait=False);self.closed=True;self.root.destroy()


def main():
    enable_legacy_tk()
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--game',type=Path,default=GAME)
    parser.add_argument('--preset',type=Path,default=DEFAULT_PRESET);args=parser.parse_args();os.chdir(ROOT)
    try:
        import ctypes
        ctypes.WinDLL('user32').SetProcessDPIAware()
    except(AttributeError,OSError):pass
    root=tk.Tk();NativeClient(root,args.game,args.preset);root.mainloop()


if __name__=='__main__':main()
