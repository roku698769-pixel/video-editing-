import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';

// 動作確認用の最小のテロップ（透明背景）。出方はイージング付き。
export const Sample: React.FC<{text: string}> = ({text}) => {
  const f = useCurrentFrame();
  const t = interpolate(f, [0, 18], [0, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
      <div style={{color: 'white', fontFamily: 'Inter, sans-serif', fontSize: 72, fontWeight: 600,
        opacity: t, transform: `translateY(${(1 - t) * 40}px)`, filter: `blur(${(1 - t) * 8}px)`,
        textShadow: '0 3px 12px rgba(0,0,0,0.45)'}}>{text}</div>
    </AbsoluteFill>
  );
};
