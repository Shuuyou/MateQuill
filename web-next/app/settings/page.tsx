import SiteHeader from "@/components/SiteHeader";
import SettingsForm from "@/components/SettingsForm";

export default function SettingsPage() {
  return (
    <main className="min-h-dvh">
      <SiteHeader />
      <div className="wrap py-14">
        <h1 className="display mt-3 text-[40px]">连接设置</h1>
        <p className="mt-4 max-w-[42em] text-[17px] leading-[1.9] text-[#3d4238]">
          密钥只保存在服务进程内存，重启即失效，不会写入日志或浏览器存储。
          线上部署请改用服务端 Secrets。
        </p>
        <SettingsForm />
      </div>
    </main>
  );
}
