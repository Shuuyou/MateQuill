"""Build a narrated product walkthrough from verified application screenshots.

Requires Windows SAPI, Pillow and imageio-ffmpeg for this authoring workstation.
No app state, credentials, model outputs or product screenshots are fabricated.
Screenshots are supplied by the real browser acceptance run in --screenshots.
"""
from __future__ import annotations
import argparse,json,re,subprocess,wave
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,ImageOps

ROOT=Path(__file__).resolve().parents[1]
SCENES=[
 ('今天的故事，\n值得好好讲。','让销售员从日常工作开始创作。','home-desktop-final.png',
  '门店每天接待顾客，也要定期写推广内容。MateQuill，把这些日常工作接起来。拍一张，说一句，让眼前的好故事，讲给真正关心它的人。'),
 ('拍一张，\n说一句。','照片理解 · 语音转写 · 确认后加入需求','photo-voice-confirmation.png',
  '销售员可以上传手边的照片，也可以说出今天的创作需求。系统读出场景，转写录音。确认这段话，就能继续。这里展示的是本次实测的照片识别和语音转写，示范录音由合成语音制作。'),
 ('缺什么，\n才问什么。','保留原话，只补上必要的一个选择。','clarification-desktop.png',
  '不必先填一张长表。用户已经说清楚的人群和任务，会直接保留。遇到同一系列的不同版本，才请销售员选一下。交互跟着问题出现，首页保持简单。'),
 ('同一款新品，\n千店千面。','人群 × 任务 × 叙事 × 画面','three-store-campaign.png',
  '校园店、商务店和创作店，面对的顾客不同。MateQuill保存各店画像，把同一推广任务，拆成不同的选题、叙事和画面。每个方向都能继续生成成稿。画面中的三家门店，是本次明确标注的演示配置。'),
 ('新品一到，\n内容就能跟上。','官方原文预览 → 事实确认 → 持续入库','new-product-facts.png',
  '新品知识不必等待模型更新。加入最新产品资料，预览型号、事实和原文，再确认入库。内容创作由最新资料承接，每项产品信息可以追到具体来源。电脑、手机和平板，沿用同一套知识流程。'),
 ('从一台设备，\n讲完整一件事。','围绕顾客任务组织华为生态内容。','ecosystem-result-final.png',
  '社团策划从手机收集灵感，到电脑写成方案，再用平板通读。生态内容围绕这个完整任务展开。每项参数归属于对应设备，跨设备剪贴板关联具体操作条件，让功能介绍变成清楚的使用方法。'),
 ('这张海报，\n由你定稿。','修改文字、字体和位置 · 撤销重做 · 导出','poster-editor-final.png',
  '内容生成之后，销售员可以继续决定它的样子。海报里的文字、字号、颜色和位置都能调整，支持撤销重做。PNG用于分享，SVG和设计文档保留后续编辑空间。文字和画面，最终交付在一起。'),
 ('好体验，\n也要有好工程。','Next.js 16 · 类型化接口 · 模块化工作流','home-desktop-final.png',
  '前端采用Next.js十六，媒体输入、门店策划、新品知识和生成工作流分开维护。事实、创意和审校各有职责，记录与画像持久保存。自动化测试和持续集成，让产品可以在复赛之后继续迭代。'),
 ('让每家店，\n有自己的表达。','MateQuill · 场景有据','home-desktop-final.png',
  'MateQuill，让销售员少准备一点，让每家店多一点自己的表达。我们是Shuuyou和6TDooo。期待让门店里的好故事，被更多人看见。'),
]

def wrap(text,font,width,draw):
 lines=[]
 for paragraph in text.split('\n'):
  line=''
  for char in paragraph:
   if line and draw.textlength(line+char,font=font)>width:lines.append(line);line=char
   else:line+=char
  lines.append(line)
 return lines

def srt_time(seconds):
 ms=round(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
 return f'{h:02}:{m:02}:{s:02},{ms:03}'

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--screenshots',type=Path,required=True)
 parser.add_argument('--output',type=Path,default=ROOT/'output/semifinal')
 args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
 missing=[name for _,_,name,_ in SCENES if not (args.screenshots/name).is_file()]
 if missing:raise SystemExit('Capture required real app screenshots first: '+', '.join(sorted(set(missing))))
 import win32com.client,imageio_ffmpeg
 ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
 voice=win32com.client.Dispatch('SAPI.SpVoice')
 voice.Voice=next(item for item in voice.GetVoices() if 'Huihui' in item.GetDescription())
 voice.Rate=1
 body=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',32)
 small=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',24)
 title_font=ImageFont.truetype('C:/Windows/Fonts/simsun.ttc',62)
 brand=ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc',34)
 timeline=[];subtitles=[];elapsed=0
 for number,(title,summary,screenshot,narration) in enumerate(SCENES,1):
  image=Image.new('RGB',(1920,1080),'#f8f7f1');draw=ImageDraw.Draw(image)
  draw.rounded_rectangle((74,62,126,114),radius=16,fill='#d6ed86')
  draw.text((147,65),'MateQuill',font=brand,fill='#242b23')
  draw.text((1640,73),f'{number:02} / {len(SCENES):02}',font=small,fill='#52614f')
  y=260
  for line in wrap(title,title_font,575,draw):draw.text((82,y),line,font=title_font,fill='#242b23');y+=88
  y+=38
  for line in wrap(summary,body,550,draw):draw.text((86,y),line,font=body,fill='#52614f');y+=55
  shot=Image.open(args.screenshots/screenshot).convert('RGB')
  # Keep the real screenshot intact; portrait captures are fitted with margins.
  shot=ImageOps.contain(shot,(1130,720),Image.Resampling.LANCZOS)
  x=700+(1130-shot.width)//2;y=190+(720-shot.height)//2
  draw.rounded_rectangle((x-2,y-2,x+shot.width+2,y+shot.height+2),radius=10,fill='#dedfd4')
  image.paste(shot,(x,y))
  draw.line((82,960,1835,960),fill='#dce0d2',width=2)
  draw.text((84,986),'产品实机画面 · 讲解剪辑',font=small,fill='#52614f')
  draw.text((1395,986),'浙客松 · 华为产业赛道复赛',font=small,fill='#52614f')
  frame=args.output/f'scene-{number:02}.png';image.save(frame)
  audio=args.output/f'voice-{number:02}.wav';stream=win32com.client.Dispatch('SAPI.SpFileStream')
  stream.Open(str(audio.resolve()),3);voice.AudioOutputStream=stream;voice.Speak(narration);stream.Close()
  with wave.open(str(audio)) as recording:spoken=recording.getnframes()/recording.getframerate()
  duration=spoken+1.8
  fragments=[x for x in re.split(r'(?<=[。！？])',narration) if x]
  local=elapsed+.3
  for fragment in fragments:
   length=spoken*len(fragment)/len(narration)
   subtitles.append((local,local+length,fragment));local+=length
  clip=args.output/f'clip-{number:02}.mp4'
  subprocess.run([ffmpeg,'-y','-hide_banner','-loglevel','error','-loop','1','-i',str(frame),'-i',str(audio),'-vf',f'fade=t=in:st=0:d=0.45,fade=t=out:st={duration-.5}:d=0.5',
   '-af','apad=pad_dur=1.8','-t',str(duration),'-c:v','libx264','-preset','veryfast','-tune','stillimage','-crf','23','-r','25','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k',str(clip)],check=True)
  timeline.append({'scene':number,'start':elapsed,'duration':duration,'source':screenshot,'narration':narration});elapsed+=duration
  print('Rendered scene',number,round(duration,1),'seconds',flush=True)
 listing=args.output/'clips.txt';listing.write_text('\n'.join("file '"+f'clip-{n:02}.mp4'+"'" for n in range(1,len(SCENES)+1)),encoding='utf8')
 srt=args.output/'MateQuill-复赛演示.srt'
 srt.write_text('\n\n'.join(f'{i}\n{srt_time(a)} --> {srt_time(b)}\n{text}' for i,(a,b,text) in enumerate(subtitles,1)),encoding='utf8')
 silent=args.output/'narrated-master.mp4'
 subprocess.run([ffmpeg,'-y','-hide_banner','-loglevel','error','-f','concat','-safe','0','-i',str(listing),'-c','copy',str(silent)],check=True)
 target=args.output/'MateQuill-复赛演示.mp4'
 subprocess.run([ffmpeg,'-y','-hide_banner','-loglevel','error','-i',str(silent),'-i',str(srt),'-map','0:v','-map','0:a','-map','1:0','-c:v','copy','-c:a','copy','-c:s','mov_text','-metadata:s:s:0','language=zho','-movflags','+faststart',str(target)],check=True)
 (args.output/'timeline.json').write_text(json.dumps({'duration_seconds':elapsed,'format':'实机截图讲解剪辑，合成配音','scenes':timeline},ensure_ascii=False,indent=2),encoding='utf8')
 print('Video ready:',target,'duration',round(elapsed,1),flush=True)

if __name__=='__main__':main()
