import React from 'react';
import {Composition} from 'remotion';
import {Sample} from './Sample';

// 案件ごとのコンポジションはここに追加する。重ね素材は透明背景で書き出す（CLAUDE.md の Remotion の節）。
export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Sample" component={Sample} durationInFrames={60} fps={30} width={1080} height={1920}
      defaultProps={{text: 'Remotion test'}} />
  </>
);
