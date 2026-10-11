const currency = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });

/** Render public monthly salary in rubles; null means no candidate upper cap. */
export function showcaseSalary(value: { from_rub: number; to_rub: number | null }): string {
  const lower = currency.format(value.from_rub / 1000);
  return value.to_rub === null ? `от ${lower}к ₽` : `${lower}–${currency.format(value.to_rub / 1000)}к ₽`;
}
