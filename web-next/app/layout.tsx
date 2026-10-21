import type { Metadata } from "next";
import "./globals.css";
import "./product.css";
import "./workspace.css";
import WorkspaceProvider from "@/components/WorkspaceProvider";

export const metadata: Metadata = {
  title: "MateQuill · 场景有据",
  description: "华为门店的随身内容搭档。拍一张，说一句，把好故事讲给对的人。",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>
        <link rel="stylesheet" href="/fonts/mingcha-ui.css" />
        <link rel="stylesheet" href="/legacy/motion.css" />
        <WorkspaceProvider>{children}</WorkspaceProvider>
      </body>
    </html>
  );
}
