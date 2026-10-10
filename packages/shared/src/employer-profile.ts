/** Account-owned company identity; invitation terms retain their original snapshot. */
export interface EmployerProfileView {
  company_name: string | null;
  revision: number;
  updated_at: string | null;
}
/** Set company identity using revision zero for initial setup and CAS thereafter. */
export interface EmployerProfileMutation {
  expected_revision: number;
  company_name: string;
}
