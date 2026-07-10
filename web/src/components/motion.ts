// 共享的 framer-motion 过渡常量，全站动画风格统一。
// 只补 MUI 自带过渡覆盖不到的部分（列表增删、布局位移、入场/退场）。
import type { Variants, Transition } from "framer-motion";

// 通用弹性过渡，偏克制不夸张
export const springSoft: Transition = { type: "spring", stiffness: 500, damping: 40, mass: 0.8 };
export const easeSoft: Transition = { duration: 0.28, ease: [0.22, 1, 0.36, 1] };

// 列表项：新增淡入下滑、删除淡出收起，配合 layout 让相邻项平滑让位
export const listItemVariants: Variants = {
  initial: { opacity: 0, y: -8, scale: 0.98 },
  animate: { opacity: 1, y: 0, scale: 1, transition: easeSoft },
  exit: { opacity: 0, x: 12, scale: 0.96, transition: { duration: 0.2, ease: "easeIn" } },
};

// 聊天气泡：淡入上移
export const bubbleVariants: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: easeSoft },
};

// 路由页面切换：淡入 + 轻微位移
export const pageVariants: Variants = {
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.24, ease: [0.22, 1, 0.36, 1] } },
  exit: { opacity: 0, y: -8, transition: { duration: 0.16, ease: "easeIn" } },
};

// 卡片/引导块入场
export const fadeUpVariants: Variants = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0, transition: easeSoft },
};
