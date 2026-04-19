/**
 * TypeScript definitions for djust-theming
 *
 * Provides comprehensive type safety and IntelliSense support
 * for all design systems, color presets, and theme tokens.
 */

// =============================================================================
// Color Scale & Theme Tokens
// =============================================================================

export interface ColorScale {
  h: number;      // Hue 0-360
  s: number;      // Saturation 0-100
  l: number;      // Lightness 0-100
  to_hsl(): string;
  to_hsl_func(): string;
}

export interface ThemeTokens {
  background: ColorScale;
  foreground: ColorScale;
  card: ColorScale;
  card_foreground: ColorScale;
  popover: ColorScale;
  popover_foreground: ColorScale;
  primary: ColorScale;
  primary_foreground: ColorScale;
  secondary: ColorScale;
  secondary_foreground: ColorScale;
  muted: ColorScale;
  muted_foreground: ColorScale;
  accent: ColorScale;
  accent_foreground: ColorScale;
  destructive: ColorScale;
  destructive_foreground: ColorScale;
  success: ColorScale;
  success_foreground: ColorScale;
  warning: ColorScale;
  warning_foreground: ColorScale;
  border: ColorScale;
  input: ColorScale;
  ring: ColorScale;
}

export interface ThemePreset {
  name: string;
  display_name: string;
  description: string;
  light: ThemeTokens;
  dark: ThemeTokens;
  radius: number;
}

// =============================================================================
// Design System Types
// =============================================================================

export type FontFamily = "system-ui" | "serif" | "mono" | "display";
export type FontWeight = "300" | "400" | "500" | "600" | "700" | "800" | "900";
export type LetterSpacing = "tight" | "normal" | "wide";

export interface TypographyStyle {
  name: string;
  heading_font: FontFamily;
  body_font: FontFamily;
  base_size: string;
  heading_scale: number;
  line_height: string;
  heading_weight: FontWeight;
  body_weight: FontWeight;
  letter_spacing: LetterSpacing;
}

export type ComponentShape = "sharp" | "rounded" | "pill" | "organic";

export interface LayoutStyle {
  name: string;
  space_unit: string;
  space_scale: number;
  border_radius_sm: string;
  border_radius_md: string;
  border_radius_lg: string;
  button_shape: ComponentShape;
  card_shape: ComponentShape;
  input_shape: ComponentShape;
  container_width: string;
  grid_gap: string;
  section_spacing: string;
}

export type BorderStyle = "solid" | "dashed" | "dotted" | "none";
export type SurfaceTreatment = "flat" | "glass" | "textured" | "gradient";

export interface SurfaceStyle {
  name: string;
  shadow_sm: string;
  shadow_md: string;
  shadow_lg: string;
  border_width: string;
  border_style: BorderStyle;
  surface_treatment: SurfaceTreatment;
  backdrop_blur: string;
  noise_opacity: number;
}

export type IconStyleType = "outlined" | "filled" | "rounded" | "sharp" | "duotone";
export type IconWeight = "thin" | "regular" | "bold";

export interface IconStyle {
  name: string;
  style: IconStyleType;
  weight: IconWeight;
  size_scale: number;
  stroke_width: string;
  corner_rounding: string;
}

export type AnimationEffect = "fade" | "slide" | "scale" | "bounce" | "none";
export type HoverEffect = "lift" | "scale" | "glow" | "none";
export type ClickEffect = "ripple" | "pulse" | "bounce" | "none";
export type LoadingStyle = "spinner" | "skeleton" | "progress" | "pulse";
export type TransitionStyle = "smooth" | "snappy" | "bouncy" | "instant";

export interface AnimationStyle {
  name: string;
  entrance_effect: AnimationEffect;
  exit_effect: AnimationEffect;
  hover_effect: HoverEffect;
  hover_scale: number;
  hover_translate_y: string;
  click_effect: ClickEffect;
  loading_style: LoadingStyle;
  transition_style: TransitionStyle;
  duration_fast: string;
  duration_normal: string;
  duration_slow: string;
  easing: string;
}

export type ButtonHover = "lift" | "scale" | "glow" | "darken" | "none";
export type LinkHover = "underline" | "color" | "background" | "none";
export type CardHover = "lift" | "scale" | "border" | "shadow" | "none";
export type FocusStyle = "ring" | "outline" | "glow" | "underline";

export interface InteractionStyle {
  name: string;
  button_hover: ButtonHover;
  link_hover: LinkHover;
  card_hover: CardHover;
  focus_style: FocusStyle;
  focus_ring_width: string;
}

export type DesignCategory = "minimal" | "bold" | "elegant" | "playful" | "industrial";

export interface DesignSystem {
  name: string;
  display_name: string;
  description: string;
  category: DesignCategory;
  typography: TypographyStyle;
  layout: LayoutStyle;
  surface: SurfaceStyle;
  icons: IconStyle;
  animation: AnimationStyle;
  interaction: InteractionStyle;
}

// =============================================================================
// Theme System Types
// =============================================================================

export type DesignSystemName =
  | "minimal"
  | "brutalist"
  | "elegant"
  | "retro"
  | "organic";

export type ColorPresetName =
  | "default"
  | "shadcn"
  | "blue"
  | "green"
  | "purple"
  | "orange"
  | "rose"
  | "cyberpunk"
  | "sunset"
  | "forest"
  | "ocean"
  | "metallic";

export interface ThemeCombination {
  design_system: DesignSystemName;
  color_preset: ColorPresetName;
}

// =============================================================================
// CSS Variables
// =============================================================================

export interface DesignSystemCSSVariables {
  // Typography
  '--font-heading': string;
  '--font-body': string;
  '--font-size-base': string;
  '--font-scale': string;
  '--line-height': string;
  '--font-weight-heading': string;
  '--font-weight-body': string;
  '--letter-spacing': string;

  // Layout
  '--space-unit': string;
  '--space-scale': string;
  '--border-radius-sm': string;
  '--border-radius-md': string;
  '--border-radius-lg': string;
  '--container-width': string;
  '--grid-gap': string;
  '--section-spacing': string;
  '--button-radius': string;
  '--card-radius': string;
  '--input-radius': string;

  // Surface
  '--shadow-sm': string;
  '--shadow-md': string;
  '--shadow-lg': string;
  '--border-width': string;
  '--border-style': string;

  // Animation
  '--duration-fast': string;
  '--duration-normal': string;
  '--duration-slow': string;
  '--easing': string;

  // Colors (from existing ThemeCSSGenerator)
  '--background': string;
  '--foreground': string;
  '--card': string;
  '--card-foreground': string;
  '--popover': string;
  '--popover-foreground': string;
  '--primary': string;
  '--primary-foreground': string;
  '--secondary': string;
  '--secondary-foreground': string;
  '--muted': string;
  '--muted-foreground': string;
  '--accent': string;
  '--accent-foreground': string;
  '--destructive': string;
  '--destructive-foreground': string;
  '--success': string;
  '--success-foreground': string;
  '--warning': string;
  '--warning-foreground': string;
  '--border': string;
  '--input': string;
  '--ring': string;
  '--radius': string;
}

// =============================================================================
// API Types
// =============================================================================

export interface ThemeManager {
  get_state(): {
    design_system?: DesignSystemName;
    color_preset?: ColorPresetName;
    theme?: string;
    preset?: string;
    pack?: string;
  };

  set_design_system(name: DesignSystemName): void;
  set_color_preset(name: ColorPresetName): void;
  set_combination(design: DesignSystemName, color: ColorPresetName): void;
}

export interface CSSGenerator {
  generate_design_system_css(
    design_system_name: DesignSystemName,
    color_preset_name: ColorPresetName,
    include_base_styles?: boolean,
    include_utilities?: boolean
  ): string;

  generate_for_combination(
    design_system_name: DesignSystemName,
    color_preset_name: ColorPresetName
  ): string;

  generate_all_combinations_css(): Record<string, string>;
}

// =============================================================================
// Utility Types
// =============================================================================

export type ThemePresetRegistry = Record<ColorPresetName, ThemePreset>;
export type DesignSystemRegistry = Record<DesignSystemName, DesignSystem>;

// Helper type for theme customization
export type DeepPartial<T> = {
  [P in keyof T]?: T[P] extends object ? DeepPartial<T[P]> : T[P];
};

export type CustomThemeTokens = DeepPartial<ThemeTokens>;
export type CustomDesignSystem = DeepPartial<DesignSystem>;

// =============================================================================
// Component Props Types (for React/Vue/etc)
// =============================================================================

export interface ThemeProviderProps {
  design_system?: DesignSystemName;
  color_preset?: ColorPresetName;
  custom_tokens?: CustomThemeTokens;
  css_variables?: boolean;
  children: React.ReactNode;
}

export interface useThemeHookReturn {
  design_system: DesignSystemName;
  color_preset: ColorPresetName;
  tokens: ThemeTokens;
  css_variables: DesignSystemCSSVariables;
  set_design_system: (name: DesignSystemName) => void;
  set_color_preset: (name: ColorPresetName) => void;
  set_combination: (design: DesignSystemName, color: ColorPresetName) => void;
}

// =============================================================================
// Django Integration Types
// =============================================================================

export interface DjangoThemeContext {
  title: string;
  theme_state: {
    design_system?: DesignSystemName;
    color_preset?: ColorPresetName;
    theme?: string;
    preset?: string;
    pack?: string;
  };
  designs: Array<{
    name: DesignSystemName;
    display_name: string;
    description: string;
    category: DesignCategory;
  }>;
  colors: Array<{
    name: ColorPresetName;
    display_name: string;
    light_primary: string;
    dark_primary: string;
  }>;
  current_design?: DesignSystemName;
  current_color?: ColorPresetName;
  current_css?: string;
}

// =============================================================================
// Global Type Augmentation (for CSS-in-JS libraries)
// =============================================================================

declare module 'styled-components' {
  export interface DefaultTheme extends DesignSystemCSSVariables {}
}

declare module '@emotion/react' {
  export interface Theme extends DesignSystemCSSVariables {}
}

// =============================================================================
// Exports
// =============================================================================

export {
  // Core types
  ColorScale,
  ThemeTokens,
  ThemePreset,
  DesignSystem,

  // Style types
  TypographyStyle,
  LayoutStyle,
  SurfaceStyle,
  IconStyle,
  AnimationStyle,
  InteractionStyle,

  // Enum types
  DesignSystemName,
  ColorPresetName,
  DesignCategory,
  ComponentShape,
  FontFamily,
  FontWeight,
  LetterSpacing,

  // API types
  ThemeManager,
  CSSGenerator,
  ThemeCombination,

  // Utility types
  CustomThemeTokens,
  CustomDesignSystem,
  DeepPartial,

  // React/Framework types
  ThemeProviderProps,
  useThemeHookReturn,

  // Django types
  DjangoThemeContext
};
