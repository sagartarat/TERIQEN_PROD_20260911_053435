function clearSlaRuleForm() {
        const form = document.getElementById('slaRuleForm');
        if (!form) return;

        const ruleCodeEl = document.getElementById('rule_code');
        const ruleNameEl = document.getElementById('rule_name');
        const ruleIdEl = document.getElementById('rule_id');
        const linkedRuleEl = document.getElementById('linked_sla_rule');
        const formActionEl = document.getElementById('form_action');

        if (ruleCodeEl) {
            ruleCodeEl.removeAttribute('readonly');
            ruleCodeEl.value = '';
        }

        if (ruleNameEl) {
            ruleNameEl.removeAttribute('readonly');
            ruleNameEl.value = '';
        }

        form.querySelectorAll('select, textarea, input[type="text"], input[type="number"], input[type="email"], input[type="date"]').forEach(function(field) {
            const fieldId = field.id || '';
            const fieldName = field.name || '';

            if (fieldId === 'rule_code' || fieldId === 'rule_name') {
                return;
            }

            if (field.tagName.toLowerCase() === 'select') {
                const hasAllOption = Array.from(field.options).some(opt => opt.value === 'All');
                if (hasAllOption) {
                    field.value = 'All';
                } else {
                    field.selectedIndex = 0;
                }
            } else {
                field.value = '';
            }
        });

        if (ruleIdEl) {
            ruleIdEl.value = '';
        }

        if (linkedRuleEl) {
            linkedRuleEl.value = '';
        }

        if (formActionEl) {
            formActionEl.value = 'save';
        }
    }
