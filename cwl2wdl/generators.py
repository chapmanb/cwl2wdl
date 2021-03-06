"""
Generator classes for WDL
"""
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import re


class WdlTaskGenerator(object):
    def __init__(self, task, structs=None):
        self.template = """
task %s {
    %s

    command {
        %s
    }

    output {
        %s
    }

    runtime {
        %s
    }
}
"""
        self.name = task.name
        self.inputs = task.inputs
        self.command = task.command
        self.outputs = task.outputs
        self.requirements = task.requirements
        self.stdin = task.stdin
        self.structs = structs if structs else []
        self.stdout = task.stdout

    def __format_inputs(self):
        inputs = []
        template = "%s %s"
        for var in self.inputs:
            if var.is_required:
                variable_type = var.variable_type
            else:
                variable_type = re.sub("($)", "?", var.variable_type)

            inputs.append(template % (variable_type,
                                      var.name))
        return "\n    ".join(inputs)

    def _is_struct(self, vtype):
        """Check if a variable is a struct, in which case we dump to output file.
        """
        vtype = vtype.replace("Array[", "").replace("]", "")
        return vtype in [x.name for x in self.structs]

    def __format_command(self):
        command_position = [0]
        command_parts = [self.command.baseCommand]

        for arg in self.command.arguments:
            command_position.append(arg.position)

            if arg.prefix is not None:
                arg_template = "%s %s" if arg.separate else "%s%s"
                prefix = arg.prefix
            else:
                arg_template = "%s%s"
                prefix = ""

            if arg.value is not None:
                value = arg.value
            else:
                value = ""

            formatted_arg = arg_template % (prefix, value)
            command_parts.append(formatted_arg)

        for command_input in self.command.inputs:
            # Some CWL inputs map be mapped to expressions
            # not quite sure how to handle these situations yet
            if command_input.variable_type == 'Boolean':
                if command_input.default == "False":
                    continue
                elif command_input.prefix is None:
                    continue
                else:
                    pass

            # Dump structs to a serialized JSON file using standard library function
            if self._is_struct(command_input.variable_type):
                command_position.append(command_input.position)
                command_parts.append("${write_struct(%s)}" % command_input.name)
                continue

            if command_input.name == self.stdout:
                continue

            # more standard cases
            if command_input.prefix is None:
                prefix = ""
                command_input_template = "%s${%s}"
            else:
                prefix = command_input.prefix
                if command_input.is_required:
                    command_input_template = "%s ${%s}" if command_input.separate else "%s${%s}"
                else:
                    command_input_template = "${%s + %s}"

            if command_input.variable_type.startswith("Array"):
                sep = "sep=\'%s\' " % (command_input.separator)
            else:
                sep = ""

            if command_input.default is not None:
                default = "default=\'%s\' " % (command_input.default)
            else:
                default = ""

            # prefix will be handled in the same way in the future
            # wdl4s and cromwell dont support this yet
            name = sep + default + command_input.name

            # store input postion if provided.
            # inputs come after the base command and arguments
            command_position.append(command_input.position)

            command_parts.append(
                command_input_template % (prefix, name)
            )

        cmd_order = [i[0] for i in sorted(enumerate(command_position),
                                          key=lambda x: (x[1] is None, x[1]))]
        ordered_command_parts = [command_parts[i] for i in cmd_order]

        for req in self.requirements:
            if req.requirement_type == "envVar":
                ordered_command_parts.insert(
                    0,
                    " ".join(["%s=\'%s\'" % (envvar[0], envvar[1]) for envvar in req.value])
                )

        # check if stdout is supposed to be captured to a file
        if self.stdout is not None:
            ordered_command_parts.append("> ${%s}" % (self.stdout))

        return " \\\n        ".join(ordered_command_parts)

    def __format_outputs(self):
        outputs = []
        template = "%s %s = %s"
        for var in self.outputs:
            outputs.append(template % (var.variable_type,
                                       var.name,
                                       var.output))
        return "\n        ".join(outputs)

    def __format_runtime(self):
        template = "%s: \'%s\'"
        requirements = []
        for requirement in self.requirements:
            if (requirement.requirement_type is None) or (requirement.value is None) or (requirement.requirement_type == "envVar"):
                continue
            else:
                requirements.append(template % (requirement.requirement_type,
                                                requirement.value))
        return "\n        ".join(requirements)

    def generate_wdl(self):
        wdl = self.template % (self.name, self.__format_inputs(),
                               self.__format_command(), self.__format_outputs(),
                               self.__format_runtime())

        # if no relavant runtime variables are specified remove that
        # section from the template
        if self.__format_runtime() == '':
            no_runtime = "\s+runtime {\s+}"
            wdl = re.sub(no_runtime, "", wdl)

        return wdl


class WdlWorkflowGenerator(object):
    def __init__(self, workflow):
        self.template = """
%s
%s

workflow %s {
    %s
    %s
    %s
}
%s
"""
        self.name = workflow.name
        self.inputs = workflow.inputs
        self.outputs = workflow.outputs
        self.steps = workflow.steps
        self.subworkflows = workflow.subworkflows
        self.structs = workflow.structs
        self.task_ids = []
        self.imported_wfs = []
        self.prepped_tasks = []

    def __format_inputs(self):
        inputs = []
        template = "{0} {1}"
        for var in self.inputs:
            if var.is_required:
                variable_type = var.variable_type
            else:
                variable_type = re.sub("($)", "?", var.variable_type)

            inputs.append(template.format(variable_type,
                                          var.name))
        return "\n    ".join(inputs)

    def __format_outputs(self):
        if len(self.outputs) > 0:
            template = """
   output {
     %s
   }
"""
            outputs = []
            for outp in self.outputs:
                outputs.append("%s %s = %s" % (outp.variable_type,
                                               outp.name.split(".")[-1],
                                               outp.name))
            return template % "\n     ".join(outputs)

    def __format_steps(self):
        steps = []
        for step in self.steps + self.subworkflows:
            self.task_ids.append(step.task_id)

            if step.task_definition is not None:
                if step.step_type == "task":
                    task_gen = WdlTaskGenerator(step.task_definition, self.structs)
                    self.prepped_tasks.append(task_gen.generate_wdl())
                else:
                    base_task_id = step.task_id.split(".")[-1]
                    self.imported_wfs.append('import "%s.wdl" as %s' % (base_task_id,
                                                                        base_task_id))

            if step.inputs != []:
                step_template = """
    call %s {
        input: %s
    }
"""
                inputs = []
                for i, inp in enumerate(step.inputs):
                    pad = "          " if i > 0 else ""
                    inputs.append(
                        "%s%s=%s" % (pad, re.sub(step.task_id + "\.", "", inp.input_id),
                                     inp.value)
                    )
                steps.append(self._format_scatter(step.scatter, self._format_prescatter(step.prescatter) +
                                                  step_template % (step.task_id, ", \n".join(inputs))))
            else:
                step_template = "call %s"
                steps.append(self._format_scatter(step.scatter, step_template % (step.task_id)))
        return "\n".join(steps)

    def _format_scatter(self, scatter, body):
        """Add scatter information to a workflow step.
        """
        if scatter:
            parts = ["  " + l for l in body.split("\n")]
            scatter_parts = ["%s in %s" % (x, y) for x, y in scatter]
            template = """
    scatter (%s) {
%s
    }
"""
            body = template % ("\n             ".join(scatter_parts), "\n".join(parts))
        return body

    def _format_prescatter(self, prescatter):
        """Provide a pre-call scattering to split apart Array[Object] attributes.
        """
        out = ""
        template = """
scatter (%s in %s) {
%s
}
"""
        for base_rec, attrs in prescatter.items():
            base_rec_item = "%s_item" % base_rec.replace(".", "_")
            unpack_attrs = []
            for new_name, orig_attr, vartype in attrs:
                unpack_attrs.append("  %s %s = %s.%s" % (vartype, new_name, base_rec_item, orig_attr))
            out += template % (base_rec_item, base_rec, "\n".join(unpack_attrs))
        return out

    def __format_structs(self):
        out = ""
        for struct in self.structs:
            template = """
struct %s {
%s
}
"""
            out += template % (struct.name, ",\n".join([" %s: %s" % (k, v) for k, v in struct.fields.items()]))
        if out:
            out = "\n" + out
        return out

    def generate_wdl(self):
        inputs, steps, outputs = self.__format_inputs(), self.__format_steps(), self.__format_outputs()
        structs = self.__format_structs()
        wdl = self.template % ("\n".join(self.imported_wfs), structs,
                               self.name, inputs, steps, outputs,
                               "\n".join(self.prepped_tasks))

        return wdl
