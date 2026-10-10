-- API deployment owns accounting. Support either service migration order.
DO $$ BEGIN
    IF to_regprocedure('public.install_employer_expense_sources()') IS NOT NULL THEN
        PERFORM public.install_employer_expense_sources();
    END IF;
END $$;
