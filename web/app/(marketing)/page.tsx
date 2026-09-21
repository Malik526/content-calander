import { Hero } from "@/components/sections/Hero";
import { HowItWorks } from "@/components/sections/HowItWorks";
import { CoreBenefits } from "@/components/sections/CoreBenefits";
import { PlatformDirection } from "@/components/sections/PlatformDirection";
import { CTASection } from "@/components/sections/CTASection";

export default function HomePage() {
  return (
    <>
      <Hero />
      <HowItWorks />
      <CoreBenefits />
      <PlatformDirection />
      <CTASection />
    </>
  );
}
