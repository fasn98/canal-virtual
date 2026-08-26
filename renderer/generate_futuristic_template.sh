#!/bin/bash

ffmpeg -y \
  -f lavfi -i "color=c=#0a0f1f:s=1920x1080:d=10" \
  -filter_complex "\
    geq=lum_expr='p(X,Y)':cb_expr='128+20*sin(2*PI*X/200)':cr_expr='128+20*sin(2*PI*Y/200)', \
    drawgrid=width=80:height=80:thickness=1:color=#1e90ff@0.3, \
    drawgrid=width=40:height=40:thickness=1:color=#00ffff@0.2, \
    noise=alls=10:allf=t+u, \
    eq=contrast=1.2:saturation=1.4 \
  " \
  -c:v libx264 -preset slow -crf 18 \
  /opt/canal-virtual/renderer/template.mp4
