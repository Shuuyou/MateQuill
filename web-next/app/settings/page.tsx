import SiteHeader from "@/components/SiteHeader";
import ServiceConnections from "@/components/ServiceConnections";

export default function SettingsPage() {
  return (
    <main className="min-h-dvh">
      <SiteHeader />
      <div className="wrap py-14">
        <h1 className="display mt-3 text-[40px]">创作服务</h1>
        <p className="mt-4 max-w-[42em] text-[17px] leading-[1.9] text-[#3d4238]">
          团队共用的创作连接由平台维护。你可以在这里查看当前服务状态。
        </p>
        <ServiceConnections />
      </div>
    </main>
  );
}
